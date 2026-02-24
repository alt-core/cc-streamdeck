"""Stream Deck Daemon: socket server, device management, and request coordination.

All display items (PermissionRequest, AskUserQuestion, Fallback, Notification)
are managed in a unified list (_items). The item to display is always selected
by (priority DESC, timestamp DESC) via _select_and_display().
"""

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

# Priority levels
PRIORITY_HIGH = 3    # PermissionRequest, AskUserQuestion
PRIORITY_MEDIUM = 2  # Fallback (ExitPlanMode)
PRIORITY_LOW = 1     # Notification

# Fallback tools
FALLBACK_TOOLS = ("ExitPlanMode",)


class _DisplayItem:
    """A display item competing for the Stream Deck screen.

    All types (permission, ask, fallback, notification) use this class.
    Display selection: max(priority, timestamp) — highest priority first,
    newest first within same priority.
    """

    __slots__ = (
        "id", "priority", "timestamp", "client_pid", "item_type",
        "request", "notification_message",
        "bg_color", "header_bg", "header_fg", "body_fg",
        "done_event", "response",
        "always_active", "ask_state",
    )

    def __init__(
        self,
        *,
        id: int,
        priority: int,
        timestamp: float,
        client_pid: int,
        item_type: str,
        request=None,
        notification_message: str = "",
        bg_color: str = "black",
        header_bg: str = "#101010",
        header_fg: str = "#808080",
        body_fg: str = "white",
        done_event: threading.Event | None = None,
        response: PermissionResponse | None = None,
        always_active: bool = False,
        ask_state: _AskQuestionState | None = None,
    ):
        self.id = id
        self.priority = priority
        self.timestamp = timestamp
        self.client_pid = client_pid
        self.item_type = item_type
        self.request = request
        self.notification_message = notification_message
        self.bg_color = bg_color
        self.header_bg = header_bg
        self.header_fg = header_fg
        self.body_fg = body_fg
        self.done_event = done_event
        self.response = response
        self.always_active = always_active
        self.ask_state = ask_state


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


class Daemon:
    def __init__(self) -> None:
        self.device_state = DeviceState()
        self._server_socket: socket.socket | None = None
        self._running = False

        # Unified display queue
        self._items: list[_DisplayItem] = []
        self._items_lock = threading.Lock()
        self._current_item: _DisplayItem | None = None
        self._next_id = 0

        # Risk and instance color state
        self._settings = load_settings()
        self._risk_config: RiskConfig = load_risk_config(self._settings)
        self._seen_pids: list[int] = []
        # Guard time: ignore button presses for this duration after display switch
        self._display_guard_sec = self._settings.display_guard_ms / 1000.0
        self._minor_guard_sec = self._settings.display_minor_guard_ms / 1000.0
        self._display_time: float = 0.0  # monotonic timestamp of last display switch
        self._guard_dim = self._settings.display_guard_dim
        self._open_button = sys.platform == "darwin"
        self._guard_timer: threading.Timer | None = None

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
        self._cancel_guard_timer()
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

    # -- Connection handling --

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

            if self.device_state.status != "ready":
                response = PermissionResponse(status="no_device")
                conn.sendall(encode(response))
                return

            key_format = self.device_state.get_key_image_format()
            if key_format is None:
                response = PermissionResponse(status="no_device")
                conn.sendall(encode(response))
                return

            # Determine item type and priority
            if request.tool_name in FALLBACK_TOOLS:
                item_type = "fallback"
                priority = PRIORITY_MEDIUM
            elif request.tool_name == "AskUserQuestion":
                questions = request.tool_input.get("questions", [])
                if not questions:
                    item_type = "fallback"
                    priority = PRIORITY_MEDIUM
                else:
                    item_type = "ask"
                    priority = PRIORITY_HIGH
            else:
                item_type = "permission"
                priority = PRIORITY_HIGH

            # Compute risk and instance colors
            palette_idx = instance_palette_index(request.client_pid, self._seen_pids)
            palette = self._risk_config.instance_palette
            bg_color = palette[palette_idx % len(palette)]
            body_fg = self._risk_config.body_text_color

            if item_type == "permission":
                risk_level = assess_risk(request.tool_name, request.tool_input, self._risk_config)
                header_bg, header_fg = self._risk_config.risk_colors[risk_level]
                logger.info(
                    "Risk: %s for %s (pid=%d, instance=%d)",
                    risk_level, request.tool_name, request.client_pid, palette_idx,
                )
            else:
                header_bg = "#604000"
                header_fg = "#FFD080"

            # Build ask state if needed
            ask_state = None
            if item_type == "ask":
                questions = request.tool_input.get("questions", [])
                ask_state = _AskQuestionState(
                    questions=questions,
                    total_pages=len(questions),
                    current_page=0,
                    answers={},
                    multi_answers={},
                    is_confirm_page=False,
                )

            # Create display item
            item = _DisplayItem(
                id=self._next_id,
                priority=priority,
                timestamp=time.monotonic(),
                client_pid=request.client_pid,
                item_type=item_type,
                request=request,
                bg_color=bg_color,
                header_bg=header_bg,
                header_fg=header_fg,
                body_fg=body_fg,
                done_event=threading.Event(),
                ask_state=ask_state,
            )
            self._next_id += 1

            # Fallback (ExitPlanMode) signals the instance moved on;
            # purge any stale connected items from the same PID.
            if item_type == "fallback":
                self._purge_connected_items(request.client_pid)

            self._add_item(item)

            # Wait for resolution
            response = self._wait_for_resolution(item, conn)

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

    def _handle_notification(self, msg: NotificationMessage) -> None:
        """Handle a low-priority notification (fire-and-forget, no response).

        Also purges any stale connected items (permission, ask, fallback) from
        the same PID.  Claude Code does not kill old hook processes after a
        terminal-side response (bug #15433), so receiving a notification from
        an instance is a reliable signal that it is idle and any remaining
        connected items are stale.
        """
        enabled = self._settings.notification_types
        if enabled and msg.notification_type not in enabled:
            logger.info("Ignoring notification type: %s", msg.notification_type)
            return

        # Purge stale connected items from the same PID
        self._purge_connected_items(msg.client_pid)

        palette_idx = instance_palette_index(msg.client_pid, self._seen_pids)
        palette = self._risk_config.instance_palette
        bg_color = palette[palette_idx % len(palette)]

        item = _DisplayItem(
            id=self._next_id,
            priority=PRIORITY_LOW,
            timestamp=time.monotonic(),
            client_pid=msg.client_pid,
            item_type="notification",
            notification_message=msg.message,
            bg_color=bg_color,
            done_event=None,  # fire-and-forget
        )
        self._next_id += 1

        self._add_item(item)

        logger.info(
            "Notification stored: %s (pid=%d)", msg.notification_type, msg.client_pid
        )

    # -- Unified display queue --

    def _add_item(self, item: _DisplayItem) -> None:
        """Add item to the queue.

        Notifications supersede same-PID notifications (only one notification
        per instance). Connected items (permission, ask, fallback) coexist
        even from the same PID to support parallel sub-agents.
        """
        with self._items_lock:
            if item.client_pid and item.item_type == "notification":
                old = [
                    i for i in self._items
                    if i.client_pid == item.client_pid and i.item_type == "notification"
                ]
                for o in old:
                    self._items.remove(o)
            self._items.append(item)
        self._select_and_display()

    def _purge_connected_items(self, client_pid: int) -> None:
        """Remove all connected items (permission, ask, fallback) for a PID.

        Called when a notification arrives, signalling the instance is idle
        and any remaining connected items are stale (CC bug #15433).
        """
        with self._items_lock:
            stale = [
                i for i in self._items
                if i.client_pid == client_pid and i.item_type in ("permission", "ask", "fallback")
            ]
            for o in stale:
                self._items.remove(o)
                if o.done_event is not None:
                    o.response = PermissionResponse(
                        status="error", error_message="Purged by notification"
                    )
                    o.done_event.set()
        if stale:
            logger.info("Purged %d stale item(s) for pid=%d", len(stale), client_pid)
            self._select_and_display()

    def _remove_item(self, item: _DisplayItem) -> None:
        """Remove item from the queue and recalculate display."""
        with self._items_lock:
            if item in self._items:
                self._items.remove(item)
        self._select_and_display()

    def _select_and_display(self) -> None:
        """Select the highest-priority, newest item and display it.

        This is the single point of display decision. Called after every
        state change (add, remove, resolve).
        """
        with self._items_lock:
            if not self._items:
                best = None
            else:
                best = max(self._items, key=lambda i: (i.priority, i.timestamp))
            prev = self._current_item
            self._current_item = best

        if best is None:
            if prev is not None:
                self.device_state.clear_keys()
            self._cancel_guard_timer()
        elif best is not prev:
            self._display_time = time.monotonic()
            self._cancel_guard_timer()
            guard_sec = self._guard_for_item(best)
            dim = self._guard_dim and guard_sec > 0
            self._render_item(best, guard_active=dim)
            if dim:
                self._start_guard_timer(guard_sec, best)

    def _wait_for_resolution(self, item: _DisplayItem, conn: socket.socket) -> PermissionResponse:
        """Block until the item is resolved (button press, disconnect, timeout)."""
        assert item.done_event is not None
        elapsed = 0.0
        while elapsed < HOOK_TIMEOUT:
            if item.done_event.wait(timeout=1.0):
                return item.response or PermissionResponse(
                    status="error", error_message="No response"
                )
            elapsed += 1.0
            # Probe hook client connection
            try:
                conn.sendall(b"\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                logger.info("Hook client disconnected, clearing display")
                self._remove_item(item)
                return PermissionResponse(status="error", error_message="Client disconnected")

        # Timeout
        self._remove_item(item)
        return PermissionResponse(status="error", error_message="Timeout")

    def _cancel_guard_timer(self) -> None:
        """Cancel any pending guard expiry timer."""
        if self._guard_timer is not None:
            self._guard_timer.cancel()
            self._guard_timer = None

    def _start_guard_timer(self, delay: float, item: _DisplayItem) -> None:
        """Schedule a re-render after guard period expires."""
        def _on_guard_expired():
            self._guard_timer = None
            if self._current_item is item:
                self._render_item(item, guard_active=False)

        self._guard_timer = threading.Timer(delay, _on_guard_expired)
        self._guard_timer.daemon = True
        self._guard_timer.start()

    def _guard_for_item(self, item: _DisplayItem) -> float:
        """Return the guard duration (seconds) appropriate for this item type."""
        if item.item_type in ("fallback", "notification"):
            return self._minor_guard_sec
        return self._display_guard_sec

    @staticmethod
    def _focus_terminal(client_pid: int) -> None:
        """Focus the terminal running the given client PID (background thread)."""
        def _run():
            try:
                from .focus import focus_pid

                focus_pid(client_pid)
            except Exception:
                logger.debug("Focus failed for pid=%d", client_pid, exc_info=True)

        threading.Thread(target=_run, daemon=True).start()

    # -- Rendering --

    def _get_grid(self) -> tuple[int, int]:
        """Get grid dimensions from device or defaults."""
        grid_info = self.device_state.get_grid_layout()
        if grid_info is not None:
            grid_rows, grid_cols, _ = grid_info
            return grid_cols, grid_rows
        from .config import GRID_COLS, GRID_ROWS
        return GRID_COLS, GRID_ROWS

    def _render_item(self, item: _DisplayItem, guard_active: bool = False) -> None:
        """Render an item on the Stream Deck."""
        if self.device_state.status != "ready":
            return
        key_format = self.device_state.get_key_image_format()
        if key_format is None:
            return
        grid_cols, grid_rows = self._get_grid()

        open_key = grid_cols - 1 if self._open_button else None

        if item.item_type == "notification":
            images = render_notification(
                item.notification_message, key_format,
                bg_color=item.bg_color,
                grid_cols=grid_cols, grid_rows=grid_rows,
                open_key=open_key,
            )
        elif item.item_type == "fallback":
            images = render_fallback_message(
                item.request.tool_name, key_format,
                bg_color=item.bg_color,
                grid_cols=grid_cols, grid_rows=grid_rows,
                open_key=open_key,
            )
        elif item.item_type == "ask":
            images = self._render_ask_page(item, key_format, grid_cols, grid_rows)
        else:  # permission
            images = render_permission_request(
                item.request, key_format,
                always_active=item.always_active,
                bg_color=item.bg_color,
                header_bg_color=item.header_bg,
                header_fg_color=item.header_fg,
                body_fg_color=item.body_fg,
                grid_cols=grid_cols, grid_rows=grid_rows,
                guard_active=guard_active,
                open_key=open_key,
            )
        self.device_state.set_key_images(images)

    def _render_ask_page(
        self, item: _DisplayItem, key_format: dict, grid_cols: int, grid_rows: int,
    ) -> dict[int, bytes]:
        """Render the current AskUserQuestion page for an item."""
        state = item.ask_state
        if state is None:
            return {}

        total_keys = grid_cols * grid_rows

        if state.is_confirm_page:
            controls = {"back": "Back", "submit": "Submit"}
            return render_ask_question_page(
                options=[], selected=set(), control_buttons=controls,
                key_image_format=key_format,
                bg_color=item.bg_color,
                grid_cols=grid_cols, grid_rows=grid_rows,
            )

        q = state.questions[state.current_page]
        is_multi = q.get("multiSelect", False)
        options_data = q.get("options", [])
        max_options = total_keys - 2
        options = [opt["label"] for opt in options_data[:max_options]]
        descriptions = [opt.get("description", "") for opt in options_data[:max_options]]

        if is_multi:
            selected = state.multi_answers.get(state.current_page, set())
        else:
            ans = state.answers.get(state.current_page)
            selected = {ans} if ans else set()

        is_multi_page = state.total_pages > 1
        use_open = self._open_button and state.current_page == 0
        if not is_multi_page:
            cancel_or_open = {"open": "Go CC"} if use_open else {"cancel": "Cancel"}
            controls = {**cancel_or_open, "submit": "Submit"}
        elif state.current_page == 0:
            cancel_or_open = {"open": "Go CC"} if use_open else {"cancel": "Cancel"}
            controls = {**cancel_or_open, "next": "Next"}
        else:
            controls = {"back": "Back", "next": "Next"}

        header = q.get("header", "")
        if is_multi_page:
            page_info = f"{header}\n{state.current_page + 1}/{state.total_pages}"
        else:
            page_info = header
        page_description = q.get("question", "")

        return render_ask_question_page(
            options=options, selected=selected, control_buttons=controls,
            key_image_format=key_format, page_info=page_info,
            page_description=page_description,
            bg_color=item.bg_color, descriptions=descriptions,
            grid_cols=grid_cols, grid_rows=grid_rows,
        )

    # -- Button handling --

    def _key_callback(self, deck, key: int, state: bool) -> None:
        """Called by Stream Deck library thread when a button is pressed."""
        if not state:
            return

        item = self._current_item
        if item is None:
            return

        # Guard time: ignore presses too soon after display switch
        guard_sec = self._guard_for_item(item)
        if guard_sec > 0:
            elapsed = time.monotonic() - self._display_time
            if elapsed < guard_sec:
                return

        if item.item_type == "notification":
            if self._open_button:
                grid_cols, _ = self._get_grid()
                if key == grid_cols - 1:
                    logger.info("Go CC pressed on notification (pid=%d)", item.client_pid)
                    self._focus_terminal(item.client_pid)
            self._remove_item(item)
            return

        if item.item_type == "fallback":
            if self._open_button:
                grid_cols, _ = self._get_grid()
                if key == grid_cols - 1:
                    item.response = PermissionResponse(status="open")
                    if item.done_event is not None:
                        item.done_event.set()
                    self._remove_item(item)
                    return
            item.response = PermissionResponse(status="fallback")
            if item.done_event is not None:
                item.done_event.set()
            self._remove_item(item)
            return

        if item.item_type == "ask":
            self._handle_ask_key(item, key)
            return

        # permission
        self._handle_permission_key(item, key)

    def _handle_permission_key(self, item: _DisplayItem, key: int) -> None:
        """Handle a button press for a permission request item."""
        request = item.request
        num_choices = len(request.choices)
        grid_cols, grid_rows = self._get_grid()
        _, choice_keys = compute_layout(num_choices, grid_cols, grid_rows)

        # Go CC button: top-right key focuses terminal
        if self._open_button and key == grid_cols - 1:
            item.response = PermissionResponse(status="open")
            if item.done_event is not None:
                item.done_event.set()
            self._remove_item(item)
            return

        if key not in choice_keys:
            return

        choice_idx = choice_keys.index(key)
        if choice_idx >= num_choices:
            return

        chosen = request.choices[choice_idx]

        # Always toggle: flip state and re-render, don't complete the request
        if chosen.updated_permissions:
            item.always_active = not item.always_active
            self._render_item(item)
            return

        # Allow with Always active: send the Always choice instead
        if chosen.behavior == "allow" and item.always_active:
            always_choice = next(
                (c for c in request.choices if c.updated_permissions),
                None,
            )
            if always_choice:
                chosen = always_choice

        item.response = PermissionResponse(status="ok", chosen=chosen)
        if item.done_event is not None:
            item.done_event.set()
        self._remove_item(item)

    def _handle_ask_key(self, item: _DisplayItem, key: int) -> None:
        """Handle a button press during AskUserQuestion."""
        state = item.ask_state
        if state is None:
            return

        grid_cols, grid_rows = self._get_grid()
        total_keys = grid_cols * grid_rows
        submit_key = total_keys - 1
        cancel_key = grid_cols - 1  # top-right

        if state.is_confirm_page:
            if key == submit_key:
                self._resolve_ask_submit(item)
            elif key == cancel_key:
                state.is_confirm_page = False
                self._render_item(item)
            return

        q = state.questions[state.current_page]
        is_multi = q.get("multiSelect", False)
        options_data = q.get("options", [])
        max_options = total_keys - 2

        control_keys = {submit_key, cancel_key}
        option_keys = []
        for k in range(total_keys):
            if k not in control_keys and len(option_keys) < min(len(options_data), max_options):
                option_keys.append(k)

        is_multi_page = state.total_pages > 1

        if key == cancel_key:
            if state.current_page == 0 and self._open_button:
                # Open: focus terminal and fallback
                item.response = PermissionResponse(status="open")
                if item.done_event is not None:
                    item.done_event.set()
                self._remove_item(item)
            elif state.current_page == 0:
                # Cancel
                item.response = PermissionResponse(
                    status="error", error_message="Cancelled by user"
                )
                if item.done_event is not None:
                    item.done_event.set()
                self._remove_item(item)
            else:
                # Back
                state.current_page -= 1
                self._render_item(item)
        elif key == submit_key:
            page_answered = (
                state.current_page in state.answers
                or state.current_page in state.multi_answers
            )
            if not is_multi_page:
                if page_answered:
                    self._resolve_ask_submit(item)
            elif page_answered:
                if state.current_page < state.total_pages - 1:
                    state.current_page += 1
                    self._render_item(item)
                else:
                    state.is_confirm_page = True
                    self._render_item(item)
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
            self._render_item(item)

    def _resolve_ask_submit(self, item: _DisplayItem) -> None:
        """Resolve an AskUserQuestion item with collected answers."""
        state = item.ask_state
        if state is None:
            return
        questions = state.questions
        ask_answers: dict[str, str] = {}
        for i, q in enumerate(questions):
            question_text = q.get("question", "")
            is_multi = q.get("multiSelect", False)
            if is_multi and i in state.multi_answers:
                ask_answers[question_text] = ", ".join(sorted(state.multi_answers[i]))
            elif i in state.answers:
                ask_answers[question_text] = state.answers[i]
        item.response = PermissionResponse(status="ok", ask_answers=ask_answers)
        if item.done_event is not None:
            item.done_event.set()
        self._remove_item(item)


# -- CLI commands (unchanged) --

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
