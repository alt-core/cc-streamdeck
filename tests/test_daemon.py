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
        daemon.device_state.get_grid_layout.return_value = (2, 3, 6)

        # Create a real socket pair so sendall raises on close
        server_sock, client_sock = socket.socketpair()
        client_sock.close()  # Simulate hook process killed

        resp = daemon._process_request(sample_request, server_sock)
        server_sock.close()

        assert resp.status == "error"
        assert "disconnected" in resp.error_message.lower()
        daemon.device_state.clear_keys.assert_called_once()

    def test_cancel_by_same_client_pid(self, sample_request):
        """New request from same PPID cancels the in-progress request."""
        daemon = Daemon()
        daemon.device_state = MagicMock()
        daemon.device_state.status = "ready"
        daemon.device_state.get_key_image_format.return_value = {
            "size": (80, 80),
            "format": "BMP",
            "flip": (False, True),
            "rotation": 90,
        }
        daemon.device_state.get_grid_layout.return_value = (2, 3, 6)

        # Give the request a client_pid
        sample_request.client_pid = 12345

        # Simulate cancel after a short delay (as if a new request arrived)
        def cancel_after_delay():
            threading.Event().wait(0.3)
            daemon._cancel_event.set()

        t = threading.Thread(target=cancel_after_delay)
        t.start()

        resp = daemon._process_request(sample_request)
        t.join()

        assert resp.status == "error"
        assert "cancelled" in resp.error_message.lower()
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
        daemon.device_state.get_grid_layout.return_value = (2, 3, 6)

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


class TestDaemonFallback:
    """Tests for ExitPlanMode and other fallback tools."""

    def _make_ready_daemon(self):
        daemon = Daemon()
        daemon.device_state = MagicMock()
        daemon.device_state.status = "ready"
        daemon.device_state.get_key_image_format.return_value = {
            "size": (80, 80),
            "format": "BMP",
            "flip": (False, True),
            "rotation": 90,
        }
        daemon.device_state.get_grid_layout.return_value = (2, 3, 6)
        return daemon

    def test_exit_plan_mode_returns_fallback(self, exit_plan_mode_request):
        daemon = self._make_ready_daemon()

        # Simulate button press after short delay
        def press_button():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # Any button works

        t = threading.Thread(target=press_button)
        t.start()

        resp = daemon._process_request(exit_plan_mode_request)
        t.join()

        assert resp.status == "fallback"
        assert resp.chosen is None
        daemon.device_state.clear_keys.assert_called_once()

    def test_exit_plan_mode_any_button_dismisses(self, exit_plan_mode_request):
        daemon = self._make_ready_daemon()

        # Set up the request state as _process_fallback would
        daemon._current_request = exit_plan_mode_request
        daemon._response_event.clear()

        # Any key press (including message area keys) should dismiss
        daemon._key_callback(None, 2, True)
        assert daemon._response_event.is_set()

    def test_exit_plan_mode_key_release_ignored(self, exit_plan_mode_request):
        daemon = self._make_ready_daemon()
        daemon._current_request = exit_plan_mode_request
        daemon._response_event.clear()

        daemon._key_callback(None, 0, False)  # key-up
        assert not daemon._response_event.is_set()

    def test_exit_plan_mode_client_disconnect(self, exit_plan_mode_request):
        daemon = self._make_ready_daemon()

        server_sock, client_sock = socket.socketpair()
        client_sock.close()

        resp = daemon._process_request(exit_plan_mode_request, server_sock)
        server_sock.close()

        assert resp.status == "fallback"
        daemon.device_state.clear_keys.assert_called_once()


class TestDaemonAskQuestion:
    """Tests for AskUserQuestion interactive UI."""

    def _make_ready_daemon(self):
        daemon = Daemon()
        daemon.device_state = MagicMock()
        daemon.device_state.status = "ready"
        daemon.device_state.get_key_image_format.return_value = {
            "size": (80, 80),
            "format": "BMP",
            "flip": (False, True),
            "rotation": 90,
        }
        daemon.device_state.get_grid_layout.return_value = (2, 3, 6)
        return daemon

    def test_select_and_submit(self, ask_question_request):
        """Select option A (key 0), then Submit (key 5)."""
        daemon = self._make_ready_daemon()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # Select Option A
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._process_request(ask_question_request)
        t.join()

        assert resp.status == "ok"
        assert resp.ask_answers == {"Which approach?": "Option A"}

    def test_cancel(self, ask_question_request):
        """Cancel (key 3) returns error."""
        daemon = self._make_ready_daemon()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 3, True)  # Cancel

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._process_request(ask_question_request)
        t.join()

        assert resp.status == "error"
        assert "cancel" in resp.error_message.lower()

    def test_change_selection(self, ask_question_request):
        """Select A, then B, then Submit — should return B."""
        daemon = self._make_ready_daemon()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # Select A
            threading.Event().wait(0.2)
            daemon._key_callback(None, 1, True)  # Change to B
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._process_request(ask_question_request)
        t.join()

        assert resp.ask_answers == {"Which approach?": "Option B"}

    def test_submit_without_selection_ignored(self, ask_question_request):
        """Submit without selecting should be ignored."""
        daemon = self._make_ready_daemon()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 5, True)  # Submit (no selection)
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # Select A
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._process_request(ask_question_request)
        t.join()

        assert resp.status == "ok"
        assert resp.ask_answers == {"Which approach?": "Option A"}

    def test_multi_page_navigation(self, ask_multi_question_request):
        """Navigate through multi-page questions."""
        daemon = self._make_ready_daemon()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # Select A1 on page 1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Next
            threading.Event().wait(0.2)
            daemon._key_callback(None, 0, True)  # Select B1 on page 2
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Go to confirm
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit on confirm page

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._process_request(ask_multi_question_request)
        t.join()

        assert resp.status == "ok"
        assert resp.ask_answers == {
            "First question?": "A1",
            "Second question?": "B1",
        }

    def test_multi_page_back(self, ask_multi_question_request):
        """Back button on page 2 returns to page 1."""
        daemon = self._make_ready_daemon()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # Select A1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Next
            threading.Event().wait(0.2)
            daemon._key_callback(None, 3, True)  # Back (page 2, left-bottom = back)
            threading.Event().wait(0.2)
            daemon._key_callback(None, 1, True)  # Change to A2
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Next
            threading.Event().wait(0.2)
            daemon._key_callback(None, 0, True)  # Select B1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Confirm
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._process_request(ask_multi_question_request)
        t.join()

        assert resp.status == "ok"
        assert resp.ask_answers["First question?"] == "A2"

    def test_multi_page_cancel_on_first_page(self, ask_multi_question_request):
        """Cancel on first page cancels the entire session."""
        daemon = self._make_ready_daemon()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 3, True)  # Cancel on page 1

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._process_request(ask_multi_question_request)
        t.join()

        assert resp.status == "error"
