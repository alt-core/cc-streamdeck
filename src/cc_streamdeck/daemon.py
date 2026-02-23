"""Stream Deck Daemon: socket server, device management, and request coordination."""

from __future__ import annotations

import logging
import signal
import socket
import sys
import threading

from .config import HOOK_TIMEOUT, LOG_PATH, SOCKET_PATH
from .device import DeviceState
from .protocol import (
    PermissionResponse,
    decode_request,
    encode,
)
from .renderer import (
    compute_layout,
    render_ask_question_page,
    render_fallback_message,
    render_permission_request,
)
from .risk import RiskConfig, assess_risk, instance_palette_index, load_risk_config
from .settings import load_settings

logger = logging.getLogger(__name__)


class Daemon:
    def __init__(self) -> None:
        self.device_state = DeviceState()
        self._current_request = None
        self._response_event = threading.Event()
        self._response: PermissionResponse | None = None
        self._request_lock = threading.Lock()
        self._always_active = False
        self._cancel_event = threading.Event()
        self._current_client_pid: int = 0
        self._server_socket: socket.socket | None = None
        self._running = False
        # Risk and instance color state
        self._settings = load_settings()
        self._risk_config: RiskConfig = load_risk_config(self._settings)
        self._seen_pids: list[int] = []
        # Cached render params for rerender
        self._current_bg_color: str = "black"
        self._current_header_bg: str = "#101010"
        self._current_header_fg: str = "#808080"
        self._current_body_fg: str = "white"
        self._current_grid_cols: int = 3
        self._current_grid_rows: int = 2
        # AskUserQuestion state
        self._ask_state: _AskQuestionState | None = None

    def start(self) -> None:
        """Main entry point for the daemon."""
        self._setup_logging()
        self._setup_signals()
        self._check_existing_daemon()

        self.device_state.start_polling(self._key_callback)

        self._running = True
        self._run_server()

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(LOG_PATH),
            ],
        )

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame) -> None:
        logger.info("Received signal %d, shutting down", signum)
        self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shut down the daemon."""
        self._running = False
        self.device_state.stop()
        if self._server_socket:
            self._server_socket.close()
        self._cleanup_socket()

    def _check_existing_daemon(self) -> None:
        """Check if another daemon is already running."""
        if not SOCKET_PATH.exists():
            return
        try:
            test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_sock.connect(str(SOCKET_PATH))
            test_sock.close()
            logger.error("Another daemon is already running")
            sys.exit(1)
        except ConnectionRefusedError:
            SOCKET_PATH.unlink()
        except OSError:
            SOCKET_PATH.unlink(missing_ok=True)

    def _cleanup_socket(self) -> None:
        SOCKET_PATH.unlink(missing_ok=True)

    def _run_server(self) -> None:
        self._cleanup_socket()
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(str(SOCKET_PATH))
        self._server_socket.listen(5)
        self._server_socket.settimeout(1.0)

        logger.info("Daemon listening on %s", SOCKET_PATH)

        try:
            while self._running:
                try:
                    conn, _ = self._server_socket.accept()
                    t = threading.Thread(target=self._handle_connection, args=(conn,), daemon=True)
                    t.start()
                except socket.timeout:
                    # Check if device poll thread requested shutdown
                    if not self.device_state._running:
                        logger.info("Device poll thread requested shutdown")
                        self.shutdown()
                        break
                    continue
                except OSError:
                    break
        finally:
            self._cleanup_socket()

    def _handle_connection(self, conn: socket.socket) -> None:
        """Handle a single Hook Client connection."""
        try:
            conn.settimeout(float(HOOK_TIMEOUT + 10))
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            logger.info("Received %d bytes from hook", len(data))

            if not data:
                return

            # Check for stop command
            import json

            try:
                msg = json.loads(data.decode("utf-8").strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.info("JSON parse failed, ignoring")
                return
            if msg.get("type") == "stop":
                logger.info("Received stop command via socket")
                self.shutdown()
                return

            request = decode_request(data)
            logger.info("Processing request for tool: %s (pid=%d)", request.tool_name, request.client_pid)

            # Cancel in-progress request from the same Claude instance
            if (
                request.client_pid
                and request.client_pid == self._current_client_pid
                and self._current_request is not None
            ):
                logger.info("Cancelling previous request from same client (pid=%d)", request.client_pid)
                self._cancel_event.set()

            with self._request_lock:
                self._cancel_event.clear()
                response = self._process_request(request, conn)

            logger.info("Sending response: %s", response.status)
            conn.sendall(encode(response))
        except Exception as e:
            logger.error("Connection error: %s", e)
            try:
                err_resp = PermissionResponse(status="error", error_message=str(e))
                conn.sendall(encode(err_resp))
            except Exception:
                pass
        finally:
            conn.close()

    def _process_request(
        self, request, conn: socket.socket | None = None
    ) -> PermissionResponse:
        """Display request on Stream Deck and wait for button press.

        Monitors the hook client connection: if the client is killed
        (e.g. user responded on terminal), the display is cleared immediately.
        """
        if self.device_state.status != "ready":
            return PermissionResponse(status="no_device")

        key_format = self.device_state.get_key_image_format()
        if key_format is None:
            return PermissionResponse(status="no_device")

        # Get grid layout from device
        grid_info = self.device_state.get_grid_layout()
        if grid_info is not None:
            grid_rows, grid_cols, _ = grid_info
        else:
            from .config import GRID_COLS, GRID_ROWS
            grid_cols, grid_rows = GRID_COLS, GRID_ROWS

        # Tools that cannot be handled via hook — show fallback message
        if request.tool_name in ("ExitPlanMode",):
            return self._process_fallback(request, key_format, grid_cols, grid_rows, conn)

        # AskUserQuestion — interactive question UI
        if request.tool_name == "AskUserQuestion":
            return self._process_ask_question(request, key_format, grid_cols, grid_rows, conn)

        self._always_active = False
        self._current_request = request
        self._current_client_pid = request.client_pid
        self._current_grid_cols = grid_cols
        self._current_grid_rows = grid_rows
        self._response_event.clear()
        self._response = None

        # Compute risk and instance colors
        risk_level = assess_risk(
            request.tool_name, request.tool_input, self._risk_config
        )
        header_bg, header_fg = self._risk_config.risk_colors[risk_level]

        palette_idx = instance_palette_index(
            request.client_pid, self._seen_pids
        )
        palette = self._risk_config.instance_palette
        bg_color = palette[palette_idx % len(palette)]
        body_fg = self._risk_config.body_text_color

        # Cache for rerender
        self._current_bg_color = bg_color
        self._current_header_bg = header_bg
        self._current_header_fg = header_fg
        self._current_body_fg = body_fg

        logger.info("Risk: %s for %s (pid=%d, instance=%d)", risk_level, request.tool_name, request.client_pid, palette_idx)

        images = render_permission_request(
            request, key_format,
            bg_color=bg_color,
            header_bg_color=header_bg,
            header_fg_color=header_fg,
            body_fg_color=body_fg,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
        )
        self.device_state.set_key_images(images)

        # Poll with 1-second intervals, checking for cancel and client disconnect
        elapsed = 0.0
        cancelled = False
        client_alive = True
        while elapsed < HOOK_TIMEOUT:
            if self._response_event.wait(timeout=1.0):
                break
            elapsed += 1.0
            if self._cancel_event.is_set():
                logger.info("Request cancelled by newer request from same client")
                cancelled = True
                break
            # Probe hook client connection
            if conn is not None and client_alive:
                try:
                    conn.sendall(b"\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    logger.info("Hook client disconnected, clearing display")
                    client_alive = False
                    break

        if cancelled or not client_alive:
            self.device_state.clear_keys()
            self._current_request = None
            self._current_client_pid = 0
            return PermissionResponse(
                status="error", error_message="Cancelled" if cancelled else "Client disconnected"
            )

        if self._response_event.is_set():
            response = self._response or PermissionResponse(
                status="error", error_message="No response"
            )
        else:
            response = PermissionResponse(status="error", error_message="Timeout")

        self.device_state.clear_keys()
        self._current_request = None
        self._current_client_pid = 0

        return response

    def _key_callback(self, deck, key: int, state: bool) -> None:
        """Called by Stream Deck library thread when a button is pressed."""
        if not state:
            return

        if self._current_request is None:
            return

        # Fallback mode: any button dismisses the display
        if self._current_request.tool_name in ("ExitPlanMode",):
            self._response_event.set()
            return

        # AskUserQuestion mode: handle via _ask_state
        if self._ask_state is not None:
            self._handle_ask_key(key)
            return

        num_choices = len(self._current_request.choices)
        _, choice_keys = compute_layout(
            num_choices, self._current_grid_cols, self._current_grid_rows
        )

        if key not in choice_keys:
            return

        choice_idx = choice_keys.index(key)
        if choice_idx >= num_choices:
            return

        chosen = self._current_request.choices[choice_idx]

        # Always toggle: flip state and re-render, don't complete the request
        if chosen.updated_permissions:
            self._always_active = not self._always_active
            self._rerender_current()
            return

        # Allow with Always active: send the Always choice instead
        if chosen.behavior == "allow" and self._always_active:
            always_choice = next(
                (c for c in self._current_request.choices if c.updated_permissions),
                None,
            )
            if always_choice:
                chosen = always_choice

        self._response = PermissionResponse(status="ok", chosen=chosen)
        self._response_event.set()

    def _process_fallback(
        self,
        request,
        key_format: dict,
        grid_cols: int,
        grid_rows: int,
        conn: socket.socket | None = None,
    ) -> PermissionResponse:
        """Show 'see terminal' message and wait for any button to dismiss."""
        logger.info("Fallback display for %s (not handled via hook)", request.tool_name)

        self._current_request = request
        self._current_client_pid = request.client_pid
        self._current_grid_cols = grid_cols
        self._current_grid_rows = grid_rows
        self._response_event.clear()
        self._response = None

        images = render_fallback_message(
            request.tool_name, key_format,
            grid_cols=grid_cols, grid_rows=grid_rows,
        )
        self.device_state.set_key_images(images)

        # Wait for any button press, cancel, or client disconnect
        elapsed = 0.0
        while elapsed < HOOK_TIMEOUT:
            if self._response_event.wait(timeout=1.0):
                break
            elapsed += 1.0
            if self._cancel_event.is_set():
                break
            if conn is not None:
                try:
                    conn.sendall(b"\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break

        self.device_state.clear_keys()
        self._current_request = None
        self._current_client_pid = 0
        return PermissionResponse(status="fallback")

    def _rerender_current(self) -> None:
        """Re-render current request with updated always_active state."""
        if self._current_request is None:
            return
        key_format = self.device_state.get_key_image_format()
        if key_format is None:
            return
        images = render_permission_request(
            self._current_request, key_format, always_active=self._always_active,
            bg_color=self._current_bg_color,
            header_bg_color=self._current_header_bg,
            header_fg_color=self._current_header_fg,
            body_fg_color=self._current_body_fg,
            grid_cols=self._current_grid_cols,
            grid_rows=self._current_grid_rows,
        )
        self.device_state.set_key_images(images)

    # -- AskUserQuestion support --

    def _process_ask_question(
        self,
        request,
        key_format: dict,
        grid_cols: int,
        grid_rows: int,
        conn: socket.socket | None = None,
    ) -> PermissionResponse:
        """Interactive question UI for AskUserQuestion."""
        questions = request.tool_input.get("questions", [])
        if not questions:
            return self._process_fallback(request, key_format, grid_cols, grid_rows, conn)

        logger.info("AskUserQuestion: %d questions", len(questions))

        self._current_request = request
        self._current_client_pid = request.client_pid
        self._current_grid_cols = grid_cols
        self._current_grid_rows = grid_rows

        # Compute instance background color
        palette_idx = instance_palette_index(request.client_pid, self._seen_pids)
        palette = self._risk_config.instance_palette
        self._current_bg_color = palette[palette_idx % len(palette)]

        total_pages = len(questions)
        self._ask_state = _AskQuestionState(
            questions=questions,
            total_pages=total_pages,
            current_page=0,
            answers={},
            multi_answers={},
            is_confirm_page=False,
        )

        self._render_ask_page(key_format, grid_cols, grid_rows)

        # Wait loop: re-render on each state change, exit on submit/cancel/disconnect
        elapsed = 0.0
        while elapsed < HOOK_TIMEOUT:
            self._response_event.clear()
            if self._response_event.wait(timeout=1.0):
                state = self._ask_state
                if state is None:
                    break
                action = state.pending_action
                state.pending_action = None

                if action == "submit":
                    # Build answers dict: question_text → label(s)
                    ask_answers = {}
                    for i, q in enumerate(questions):
                        question_text = q.get("question", "")
                        is_multi = q.get("multiSelect", False)
                        if is_multi and i in state.multi_answers:
                            ask_answers[question_text] = ", ".join(sorted(state.multi_answers[i]))
                        elif i in state.answers:
                            ask_answers[question_text] = state.answers[i]
                    self.device_state.clear_keys()
                    self._current_request = None
                    self._current_client_pid = 0
                    self._ask_state = None
                    return PermissionResponse(status="ok", ask_answers=ask_answers)

                if action == "cancel":
                    self.device_state.clear_keys()
                    self._current_request = None
                    self._current_client_pid = 0
                    self._ask_state = None
                    return PermissionResponse(status="error", error_message="Cancelled by user")

                # Navigation or selection — re-render
                self._render_ask_page(key_format, grid_cols, grid_rows)
                continue

            elapsed += 1.0
            if self._cancel_event.is_set():
                break
            if conn is not None:
                try:
                    conn.sendall(b"\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break

        self.device_state.clear_keys()
        self._current_request = None
        self._current_client_pid = 0
        self._ask_state = None
        return PermissionResponse(status="error", error_message="Cancelled")

    def _render_ask_page(self, key_format: dict, grid_cols: int, grid_rows: int) -> None:
        """Render the current AskUserQuestion page."""
        state = self._ask_state
        if state is None:
            return

        total_keys = grid_cols * grid_rows
        is_multi_page = state.total_pages > 1

        if state.is_confirm_page:
            # Confirmation page: Back + Cancel + Submit, no options
            controls = {
                "back": "Back",
                "cancel": "Cancel",
                "submit": "Submit",
            }
            page_info = "Confirm"
            images = render_ask_question_page(
                options=[], selected=set(), control_buttons=controls,
                key_image_format=key_format, page_info=page_info,
                bg_color=self._current_bg_color,
                grid_cols=grid_cols, grid_rows=grid_rows,
            )
        else:
            q = state.questions[state.current_page]
            is_multi = q.get("multiSelect", False)
            options_data = q.get("options", [])
            max_options = total_keys - 2
            options = [opt["label"] for opt in options_data[:max_options]]

            # Get selected set for this page
            if is_multi:
                selected = state.multi_answers.get(state.current_page, set())
            else:
                ans = state.answers.get(state.current_page)
                selected = {ans} if ans else set()

            # Control buttons
            if not is_multi_page:
                controls = {"cancel": "Cancel", "submit": "Submit"}
            elif state.current_page == 0:
                controls = {"cancel": "Cancel", "next": "Next"}
            else:
                controls = {"back": "Back", "next": "Next"}

            page_info = f"{state.current_page + 1}/{state.total_pages}" if is_multi_page else ""

            images = render_ask_question_page(
                options=options, selected=selected, control_buttons=controls,
                key_image_format=key_format, page_info=page_info,
                bg_color=self._current_bg_color,
                grid_cols=grid_cols, grid_rows=grid_rows,
            )

        self.device_state.set_key_images(images)

    def _handle_ask_key(self, key: int) -> None:
        """Handle a button press during AskUserQuestion."""
        state = self._ask_state
        if state is None:
            return

        grid_cols = self._current_grid_cols
        grid_rows = self._current_grid_rows
        total_keys = grid_cols * grid_rows
        submit_key = total_keys - 1
        cancel_key = total_keys - grid_cols

        if state.is_confirm_page:
            # Confirm page: cancel_key=Back, cancel_key+1=Cancel, submit_key=Submit
            if key == submit_key:
                state.pending_action = "submit"
                self._response_event.set()
            elif key == cancel_key:
                # Back to last question
                state.is_confirm_page = False
                state.pending_action = "navigate"
                self._response_event.set()
            elif key == cancel_key + 1:
                state.pending_action = "cancel"
                self._response_event.set()
            return

        q = state.questions[state.current_page]
        is_multi = q.get("multiSelect", False)
        options_data = q.get("options", [])
        max_options = total_keys - 2

        # Build option key list (same logic as renderer)
        control_keys = {submit_key, cancel_key}
        option_keys = []
        for k in range(total_keys):
            if k not in control_keys and len(option_keys) < min(len(options_data), max_options):
                option_keys.append(k)

        is_multi_page = state.total_pages > 1

        if key == cancel_key:
            if state.current_page == 0:
                # Cancel on first page
                state.pending_action = "cancel"
                self._response_event.set()
            else:
                # Back
                state.current_page -= 1
                state.pending_action = "navigate"
                self._response_event.set()
        elif key == submit_key:
            if not is_multi_page:
                # Single question: Submit
                if state.current_page in state.answers or state.current_page in state.multi_answers:
                    state.pending_action = "submit"
                    self._response_event.set()
            else:
                # Multi-page: Next or go to confirm
                page_answered = state.current_page in state.answers or state.current_page in state.multi_answers
                if page_answered:
                    if state.current_page < state.total_pages - 1:
                        state.current_page += 1
                        state.pending_action = "navigate"
                        self._response_event.set()
                    else:
                        state.is_confirm_page = True
                        state.pending_action = "navigate"
                        self._response_event.set()
        elif key in option_keys:
            idx = option_keys.index(key)
            label = options_data[idx]["label"]
            if is_multi:
                multi = state.multi_answers.setdefault(state.current_page, set())
                if label in multi:
                    multi.discard(label)
                else:
                    multi.add(label)
            else:
                state.answers[state.current_page] = label
            state.pending_action = "select"
            self._response_event.set()


class _AskQuestionState:
    """Mutable state for an AskUserQuestion session."""

    __slots__ = (
        "questions", "total_pages", "current_page",
        "answers", "multi_answers", "is_confirm_page", "pending_action",
    )

    def __init__(
        self,
        questions: list,
        total_pages: int,
        current_page: int,
        answers: dict,
        multi_answers: dict,
        is_confirm_page: bool,
    ):
        self.questions = questions
        self.total_pages = total_pages
        self.current_page = current_page
        self.answers: dict[int, str] = answers
        self.multi_answers: dict[int, set[str]] = multi_answers
        self.is_confirm_page = is_confirm_page
        self.pending_action: str | None = None


def _send_stop() -> bool:
    """Send stop command to a running daemon. Returns True if successful."""
    import json

    if not SOCKET_PATH.exists():
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(str(SOCKET_PATH))
        sock.sendall((json.dumps({"type": "stop"}) + "\n").encode())
        sock.shutdown(socket.SHUT_WR)
        sock.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


def main() -> None:
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--stop":
        if _send_stop():
            print("Stop signal sent to daemon.")
        else:
            print("No running daemon found.")
        return

    daemon = Daemon()
    daemon.start()
