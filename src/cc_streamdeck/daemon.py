"""Stream Deck Daemon: socket server, device management, and request coordination."""

from __future__ import annotations

import logging
import signal
import socket
import sys
import threading
import time

from .config import HOOK_TIMEOUT, LOG_PATH, SOCKET_PATH
from .device import DeviceState
from .protocol import (
    NotificationMessage,
    PermissionResponse,
    decode_notification,
    decode_request,
    encode,
)
from .renderer import (
    compute_layout,
    render_ask_question_page,
    render_fallback_message,
    render_notification,
    render_permission_request,
)
from .risk import (
    BUILTIN_BASH_RULES,
    RiskConfig,
    assess_risk,
    assess_risk_verbose,
    instance_palette_index,
    load_risk_config,
)
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
        self._latest_request_time: dict[int, float] = {}  # pid → monotonic timestamp
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
        # Background display for low-priority notifications
        self._background_display: _BackgroundDisplay | None = None
        self._background_lock = threading.Lock()

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

            if msg.get("type") == "notification":
                self._handle_notification(decode_notification(data))
                return

            request = decode_request(data)
            logger.info(
                "Processing request for tool: %s (pid=%d)", request.tool_name, request.client_pid
            )

            # Cancel in-progress request from the same Claude instance
            if (
                request.client_pid
                and request.client_pid == self._current_client_pid
                and self._current_request is not None
            ):
                logger.info(
                    "Cancelling previous request from same client (pid=%d)", request.client_pid
                )
                self._cancel_event.set()

            # Track the latest request per PID so stale queued requests
            # can be detected after acquiring the lock.
            request_time = time.monotonic()
            if request.client_pid:
                self._latest_request_time[request.client_pid] = request_time

            # A new HIGH/MEDIUM request clears background from the same PID
            with self._background_lock:
                if (
                    self._background_display is not None
                    and self._background_display.client_pid == request.client_pid
                ):
                    self._background_display = None

            with self._request_lock:
                # If a newer request from the same PID arrived while we were
                # waiting for the lock, this request is stale — skip it.
                if (
                    request.client_pid
                    and self._latest_request_time.get(request.client_pid, 0) > request_time
                ):
                    logger.info(
                        "Skipping stale request for %s (pid=%d)", request.tool_name, request.client_pid
                    )
                    response = PermissionResponse(status="error", error_message="Stale request")
                else:
                    self._cancel_event.clear()
                    response = self._process_request(request, conn)
                    # After HIGH/MEDIUM completes, restore LOW from a different PID
                    self._restore_background()

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

    def _process_request(self, request, conn: socket.socket | None = None) -> PermissionResponse:
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
        risk_level = assess_risk(request.tool_name, request.tool_input, self._risk_config)
        header_bg, header_fg = self._risk_config.risk_colors[risk_level]

        palette_idx = instance_palette_index(request.client_pid, self._seen_pids)
        palette = self._risk_config.instance_palette
        bg_color = palette[palette_idx % len(palette)]
        body_fg = self._risk_config.body_text_color

        # Cache for rerender
        self._current_bg_color = bg_color
        self._current_header_bg = header_bg
        self._current_header_fg = header_fg
        self._current_body_fg = body_fg

        logger.info(
            "Risk: %s for %s (pid=%d, instance=%d)",
            risk_level,
            request.tool_name,
            request.client_pid,
            palette_idx,
        )

        images = render_permission_request(
            request,
            key_format,
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
            # Notification showing: any button press dismisses it
            with self._background_lock:
                if self._background_display is not None:
                    self._background_display = None
                    self.device_state.clear_keys()
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

        # Compute instance background color
        palette_idx = instance_palette_index(request.client_pid, self._seen_pids)
        palette = self._risk_config.instance_palette
        bg_color = palette[palette_idx % len(palette)]

        images = render_fallback_message(
            request.tool_name, key_format,
            bg_color=bg_color, grid_cols=grid_cols, grid_rows=grid_rows,
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
            self._current_request,
            key_format,
            always_active=self._always_active,
            bg_color=self._current_bg_color,
            header_bg_color=self._current_header_bg,
            header_fg_color=self._current_header_fg,
            body_fg_color=self._current_body_fg,
            grid_cols=self._current_grid_cols,
            grid_rows=self._current_grid_rows,
        )
        self.device_state.set_key_images(images)

    # -- Notification / background display --

    def _handle_notification(self, msg: NotificationMessage) -> None:
        """Handle a low-priority notification (fire-and-forget, no response)."""
        # Check if notification type is enabled
        enabled = self._settings.notification_types
        if enabled and msg.notification_type not in enabled:
            logger.info("Ignoring notification type: %s", msg.notification_type)
            return

        # Compute instance background color
        palette_idx = instance_palette_index(msg.client_pid, self._seen_pids)
        palette = self._risk_config.instance_palette
        bg_color = palette[palette_idx % len(palette)]

        bg = _BackgroundDisplay(
            client_pid=msg.client_pid,
            notification_type=msg.notification_type,
            message=msg.message,
            bg_color=bg_color,
            timestamp=time.monotonic(),
        )

        with self._background_lock:
            self._background_display = bg

        # Only render if no HIGH/MEDIUM request is active
        if self._current_request is None:
            self._render_background(bg)

        logger.info(
            "Notification stored: %s (pid=%d)", msg.notification_type, msg.client_pid
        )

    def _render_background(self, bg: _BackgroundDisplay) -> None:
        """Render a background notification on the device."""
        if self.device_state.status != "ready":
            return
        key_format = self.device_state.get_key_image_format()
        if key_format is None:
            return
        grid_info = self.device_state.get_grid_layout()
        if grid_info is not None:
            grid_rows, grid_cols, _ = grid_info
        else:
            from .config import GRID_COLS, GRID_ROWS
            grid_cols, grid_rows = GRID_COLS, GRID_ROWS

        images = render_notification(
            bg.message, key_format,
            bg_color=bg.bg_color,
            grid_cols=grid_cols, grid_rows=grid_rows,
        )
        self.device_state.set_key_images(images)

    def _restore_background(self) -> None:
        """Restore background display after a HIGH/MEDIUM request completes."""
        with self._background_lock:
            bg = self._background_display
        if bg is not None:
            self._render_background(bg)

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
            # Confirmation page: Back + Submit, no options
            controls = {
                "back": "Back",
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
            descriptions = [opt.get("description", "") for opt in options_data[:max_options]]

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

            header = q.get("header", "")
            page_info = header
            page_description = q.get("question", "")

            images = render_ask_question_page(
                options=options, selected=selected, control_buttons=controls,
                key_image_format=key_format, page_info=page_info,
                page_description=page_description,
                bg_color=self._current_bg_color, descriptions=descriptions,
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
            # Confirm page: cancel_key=Back, submit_key=Submit, others ignored
            if key == submit_key:
                state.pending_action = "submit"
                self._response_event.set()
            elif key == cancel_key:
                # Back to last question
                state.is_confirm_page = False
                state.pending_action = "navigate"
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


class _BackgroundDisplay:
    """Stored low-priority notification for display restoration."""

    __slots__ = ("client_pid", "notification_type", "message", "bg_color", "timestamp")

    def __init__(
        self,
        client_pid: int,
        notification_type: str,
        message: str,
        bg_color: str,
        timestamp: float,
    ):
        self.client_pid = client_pid
        self.notification_type = notification_type
        self.message = message
        self.bg_color = bg_color
        self.timestamp = timestamp


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


def _cmd_check_config() -> None:
    """Validate and display config summary."""
    from .settings import get_config_path, load_settings

    path = get_config_path()
    print(f"Config: {path}")
    if not path.exists():
        print("  (file not found, using defaults)")

    settings = load_settings()
    config = load_risk_config(settings)

    # Count built-in rules by level
    builtin_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for _, _, level in BUILTIN_BASH_RULES:
        builtin_counts[level] += 1

    # Count effective rules
    effective_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for rule in config.bash_rules:
        effective_counts[rule.level] += 1

    print(
        f"  Built-in rules: {sum(builtin_counts.values())}"
        f" ({builtin_counts['critical']} critical,"
        f" {builtin_counts['high']} high,"
        f" {builtin_counts['low']} low)"
    )
    print(f"  Prepend rules: {len(settings.bash_prepend)}")
    print(f"  Append rules: {len(settings.bash_append)}")
    print(f"  Level overrides: {len(settings.bash_levels)}")
    if settings.bash_levels:
        for name, level in settings.bash_levels.items():
            print(f"    {name} -> {level}")
    print(
        f"  Effective rules: {sum(effective_counts.values())}"
        f" ({effective_counts['critical']} critical,"
        f" {effective_counts['high']} high,"
        f" {effective_counts['medium']} medium,"
        f" {effective_counts['low']} low)"
    )
    print(f"  Path elevation: {len(config.path_critical)} critical, {len(config.path_high)} high")

    # Tool risk
    print("  Tool risk:")
    for tool, level in sorted(config.tool_risk.items()):
        print(f"    {tool} = {level}")
    print(f"    (default) = {config.tool_risk_fallback}")


def _cmd_assess(args: list[str]) -> None:
    """Dry-run risk assessment."""
    import argparse

    parser = argparse.ArgumentParser(prog="cc-streamdeck-daemon --assess")
    parser.add_argument("tool", help="Tool name (e.g. Bash, Write, Edit)")
    parser.add_argument("command", nargs="?", default="", help="Command string (for Bash)")
    parser.add_argument("--file-path", default="", help="File path (for Write/Edit)")
    parsed = parser.parse_args(args)

    settings = load_settings()
    config = load_risk_config(settings)

    tool_input: dict = {}
    if parsed.command:
        tool_input["command"] = parsed.command
    if parsed.file_path:
        tool_input["file_path"] = parsed.file_path

    level, matched = assess_risk_verbose(parsed.tool, tool_input, config)

    print(f"Tool: {parsed.tool}")
    if parsed.command:
        print(f"Command: {parsed.command}")
    if parsed.file_path:
        print(f"File path: {parsed.file_path}")
    print(f"Risk: {level}")
    if matched:
        print(f"  Matched: {matched}")
    else:
        print("  Matched: (none, using default)")


def main() -> None:
    import sys

    args = sys.argv[1:]

    if not args:
        daemon = Daemon()
        daemon.start()
        return

    cmd = args[0]

    if cmd == "--stop":
        if _send_stop():
            print("Stop signal sent to daemon.")
        else:
            print("No running daemon found.")
    elif cmd == "--check-config":
        _cmd_check_config()
    elif cmd == "--assess":
        _cmd_assess(args[1:])
    else:
        print(f"Unknown option: {cmd}", file=sys.stderr)
        print(
            "Usage: cc-streamdeck-daemon [--stop | --check-config | --assess TOOL [COMMAND] [--file-path PATH]]",
            file=sys.stderr,
        )
        sys.exit(1)
