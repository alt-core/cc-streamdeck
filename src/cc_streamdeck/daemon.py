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

            request = decode_request(data)

            with self._request_lock:
                response = self._process_request(request)

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

    def _process_request(self, request) -> PermissionResponse:
        """Display request on Stream Deck and wait for button press."""
        if self.device_state.status != "ready":
            return PermissionResponse(status="no_device")

        key_format = self.device_state.get_key_image_format()
        if key_format is None:
            return PermissionResponse(status="no_device")

        images = render_permission_request(request, key_format)
        self.device_state.set_key_images(images)

        self._current_request = request
        self._response_event.clear()
        self._response = None

        if self._response_event.wait(timeout=590.0):
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

        if key in choice_keys:
            choice_idx = choice_keys.index(key)
            if choice_idx < num_choices:
                chosen = self._current_request.choices[choice_idx]
                self._response = PermissionResponse(status="ok", chosen=chosen)
                self._response_event.set()


def main() -> None:
    daemon = Daemon()
    daemon.start()
