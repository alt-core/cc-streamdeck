"""Tests for Daemon socket communication."""

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
    def test_key_press_sets_response(self, sample_request):
        daemon = Daemon()
        daemon._current_request = sample_request
        daemon._response_event.clear()

        # Key 3 = first choice key for 3-choice layout
        daemon._key_callback(None, 3, True)

        assert daemon._response_event.is_set()
        assert daemon._response.status == "ok"
        assert daemon._response.chosen.label == "Allow"

    def test_key_release_ignored(self, sample_request):
        daemon = Daemon()
        daemon._current_request = sample_request
        daemon._response_event.clear()

        daemon._key_callback(None, 3, False)  # key-up

        assert not daemon._response_event.is_set()

    def test_no_current_request_ignored(self):
        daemon = Daemon()
        daemon._current_request = None

        daemon._key_callback(None, 3, True)

        assert not daemon._response_event.is_set()

    def test_message_key_ignored(self, sample_request):
        daemon = Daemon()
        daemon._current_request = sample_request
        daemon._response_event.clear()

        daemon._key_callback(None, 0, True)  # message area key

        assert not daemon._response_event.is_set()

    def test_deny_choice(self, sample_request):
        daemon = Daemon()
        daemon._current_request = sample_request
        daemon._response_event.clear()

        daemon._key_callback(None, 4, True)  # key 4 = Deny

        assert daemon._response.chosen.label == "Deny"
        assert daemon._response.chosen.behavior == "deny"

    def test_two_choice_layout(self, two_choice_request):
        daemon = Daemon()
        daemon._current_request = two_choice_request
        daemon._response_event.clear()

        # For 2 choices: choice_keys = [4, 5]
        daemon._key_callback(None, 4, True)

        assert daemon._response.chosen.label == "Allow"


class TestDaemonProcessRequest:
    def test_no_device_returns_status(self):
        daemon = Daemon()
        daemon.device_state = MagicMock()
        daemon.device_state.status = "no_device"

        from cc_streamdeck.protocol import PermissionRequest

        req = PermissionRequest(tool_name="Bash", tool_input={"command": "ls"})
        resp = daemon._process_request(req)
        assert resp.status == "no_device"
