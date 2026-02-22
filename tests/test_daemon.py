"""Tests for Daemon socket communication."""

import socket
import threading
from unittest.mock import MagicMock, patch

from cc_streamdeck.daemon import Daemon


class TestDaemonCheckExisting:
    def test_stale_socket_removed(self, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        with patch("cc_streamdeck.daemon.SOCKET_PATH", sock_path):
            daemon = Daemon()
            daemon._check_existing_daemon()
            assert not sock_path.exists()


class TestDaemonKeyCallback:
    def _make_daemon(self, request):
        """Create a daemon with mocked device_state for toggle tests."""
        daemon = Daemon()
        daemon._current_request = request
        daemon._response_event.clear()
        daemon.device_state = MagicMock()
        daemon.device_state.get_key_image_format.return_value = None
        return daemon

    def test_allow_key(self, sample_request):
        daemon = self._make_daemon(sample_request)

        # Key 5 = Allow (rightmost, choice_keys[0])
        daemon._key_callback(None, 5, True)

        assert daemon._response_event.is_set()
        assert daemon._response.status == "ok"
        assert daemon._response.chosen.label == "Allow"

    def test_deny_key(self, sample_request):
        daemon = self._make_daemon(sample_request)

        # Key 3 = Deny (left, choice_keys[1])
        daemon._key_callback(None, 3, True)

        assert daemon._response_event.is_set()
        assert daemon._response.chosen.label == "Deny"
        assert daemon._response.chosen.behavior == "deny"

    def test_key_release_ignored(self, sample_request):
        daemon = self._make_daemon(sample_request)

        daemon._key_callback(None, 5, False)  # key-up

        assert not daemon._response_event.is_set()

    def test_no_current_request_ignored(self):
        daemon = Daemon()
        daemon._current_request = None

        daemon._key_callback(None, 5, True)

        assert not daemon._response_event.is_set()

    def test_message_key_ignored(self, sample_request):
        daemon = self._make_daemon(sample_request)

        daemon._key_callback(None, 0, True)  # message area key

        assert not daemon._response_event.is_set()

    def test_always_toggle_on(self, sample_request):
        daemon = self._make_daemon(sample_request)

        # Key 4 = Always (middle, choice_keys[2])
        daemon._key_callback(None, 4, True)

        # Should NOT complete the request
        assert not daemon._response_event.is_set()
        assert daemon._always_active is True

    def test_always_toggle_off(self, sample_request):
        daemon = self._make_daemon(sample_request)
        daemon._always_active = True

        daemon._key_callback(None, 4, True)

        assert not daemon._response_event.is_set()
        assert daemon._always_active is False

    def test_always_then_allow(self, sample_request):
        daemon = self._make_daemon(sample_request)

        # Toggle Always on (key 4 = middle)
        daemon._key_callback(None, 4, True)
        assert daemon._always_active is True

        # Press Allow — should get the Always choice
        daemon._key_callback(None, 5, True)

        assert daemon._response_event.is_set()
        assert daemon._response.chosen.label == "Always"
        assert daemon._response.chosen.updated_permissions

    def test_deny_ignores_always(self, sample_request):
        daemon = self._make_daemon(sample_request)
        daemon._always_active = True

        # Deny always denies, regardless of Always toggle (key 3 = left)
        daemon._key_callback(None, 3, True)

        assert daemon._response_event.is_set()
        assert daemon._response.chosen.label == "Deny"
        assert daemon._response.chosen.behavior == "deny"

    def test_two_choice_allow(self, two_choice_request):
        daemon = self._make_daemon(two_choice_request)

        # For 2 choices: choice_keys = [5, 4]
        daemon._key_callback(None, 5, True)

        assert daemon._response.chosen.label == "Allow"

    def test_two_choice_deny(self, two_choice_request):
        daemon = self._make_daemon(two_choice_request)

        # For 2 choices: choice_keys = [5, 4], key 4 = Deny
        daemon._key_callback(None, 4, True)

        assert daemon._response.chosen.label == "Deny"


class TestDaemonProcessRequest:
    def test_no_device_returns_status(self):
        daemon = Daemon()
        daemon.device_state = MagicMock()
        daemon.device_state.status = "no_device"

        from cc_streamdeck.protocol import PermissionRequest

        req = PermissionRequest(tool_name="Bash", tool_input={"command": "ls"})
        resp = daemon._process_request(req)
        assert resp.status == "no_device"

    def test_client_disconnect_clears_display(self, sample_request):
        """When hook client dies, daemon detects and clears Stream Deck."""
        daemon = Daemon()
        daemon.device_state = MagicMock()
        daemon.device_state.status = "ready"
        daemon.device_state.get_key_image_format.return_value = {
            "size": (80, 80),
            "format": "BMP",
            "flip": (False, True),
            "rotation": 90,
        }

        # Create a real socket pair so sendall raises on close
        server_sock, client_sock = socket.socketpair()
        client_sock.close()  # Simulate hook process killed

        resp = daemon._process_request(sample_request, server_sock)
        server_sock.close()

        assert resp.status == "error"
        assert "disconnected" in resp.error_message.lower()
        daemon.device_state.clear_keys.assert_called_once()

    def test_button_press_still_works_with_conn(self, sample_request):
        """Button press resolves normally even when conn monitoring is active."""
        daemon = Daemon()
        daemon.device_state = MagicMock()
        daemon.device_state.status = "ready"
        daemon.device_state.get_key_image_format.return_value = {
            "size": (80, 80),
            "format": "BMP",
            "flip": (False, True),
            "rotation": 90,
        }

        server_sock, client_sock = socket.socketpair()

        # Simulate button press after a short delay
        def press_allow():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 5, True)

        t = threading.Thread(target=press_allow)
        t.start()

        resp = daemon._process_request(sample_request, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.chosen.label == "Allow"
