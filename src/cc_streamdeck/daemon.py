"""Stream Deck Daemon: socket server, device management, and request coordination."""

from __future__ import annotations

import logging
import signal
import socket
import sys
import threading

from .config import LOG_PATH, SOCKET_PATH
from .device import DeviceState
from .protocol import (
    PermissionResponse,
    decode_request,
    encode,
)
from .renderer import compute_layout, render_permission_request

logger = logging.getLogger(__name__)


class Daemon:
    def __init__(self) -> None:
        self.device_state = DeviceState()
        self._current_request = None
        self._response_event = threading.Event()
        self._response: PermissionResponse | None = None
        self._request_lock = threading.Lock()
        self._always_active = False
        self._server_socket: socket.socket | None = None
        self._running = False

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
                    continue
                except OSError:
                    break
        finally:
            self._cleanup_socket()

    def _handle_connection(self, conn: socket.socket) -> None:
        """Handle a single Hook Client connection."""
        try:
            conn.settimeout(600.0)
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            if not data:
                return

            # Check for stop command
            import json

            try:
                msg = json.loads(data.decode("utf-8").strip())
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            if msg.get("type") == "stop":
                logger.info("Received stop command via socket")
                self.shutdown()
                return

            request = decode_request(data)

            with self._request_lock:
                response = self._process_request(request, conn)

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

        self._always_active = False
        self._current_request = request
        self._response_event.clear()
        self._response = None

        images = render_permission_request(request, key_format)
        self.device_state.set_key_images(images)

        # Poll with 1-second intervals, probing client connection each time
        elapsed = 0.0
        client_alive = True
        while elapsed < 590.0:
            if self._response_event.wait(timeout=1.0):
                break
            elapsed += 1.0
            # Probe hook client connection
            if conn is not None and client_alive:
                try:
                    conn.sendall(b"\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    logger.info("Hook client disconnected, clearing display")
                    client_alive = False
                    break

        if not client_alive:
            self.device_state.clear_keys()
            self._current_request = None
            return PermissionResponse(
                status="error", error_message="Client disconnected"
            )

        if self._response_event.is_set():
            response = self._response or PermissionResponse(
                status="error", error_message="No response"
            )
        else:
            response = PermissionResponse(status="error", error_message="Timeout")

        self.device_state.clear_keys()
        self._current_request = None

        return response

    def _key_callback(self, deck, key: int, state: bool) -> None:
        """Called by Stream Deck library thread when a button is pressed."""
        if not state:
            return

        if self._current_request is None:
            return

        num_choices = len(self._current_request.choices)
        _, choice_keys = compute_layout(num_choices)

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

    def _rerender_current(self) -> None:
        """Re-render current request with updated always_active state."""
        if self._current_request is None:
            return
        key_format = self.device_state.get_key_image_format()
        if key_format is None:
            return
        images = render_permission_request(
            self._current_request, key_format, always_active=self._always_active
        )
        self.device_state.set_key_images(images)


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
