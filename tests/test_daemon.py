"""Tests for Daemon unified display queue."""

import socket
import threading
from unittest.mock import MagicMock, patch

from cc_streamdeck.daemon import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    Daemon,
    _AskQuestionState,
    _DisplayItem,
)


class TestDaemonCheckExisting:
    def test_stale_socket_removed(self, tmp_path):
        sock_path = tmp_path / "test.sock"
        sock_path.touch()
        with patch("cc_streamdeck.daemon.SOCKET_PATH", sock_path):
            daemon = Daemon()
            daemon._check_existing_daemon()
            assert not sock_path.exists()


def _make_ready_daemon():
    """Create a daemon with mocked device_state."""
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


def _make_item(daemon, request, item_type="permission", priority=PRIORITY_HIGH, client_pid=0):
    """Create a _DisplayItem for a request."""
    item = _DisplayItem(
        id=daemon._next_id,
        priority=priority,
        timestamp=0.0,
        client_pid=client_pid or request.client_pid,
        item_type=item_type,
        request=request,
        bg_color="#0A0A20",
        header_bg="#101010",
        header_fg="#808080",
        body_fg="white",
        done_event=threading.Event(),
    )
    daemon._next_id += 1
    return item


class TestDaemonKeyCallback:
    def _setup(self, request, item_type="permission", priority=PRIORITY_HIGH):
        daemon = _make_ready_daemon()
        item = _make_item(daemon, request, item_type=item_type, priority=priority)
        daemon._items.append(item)
        daemon._current_item = item
        return daemon, item

    def test_allow_key(self, sample_request):
        daemon, item = self._setup(sample_request)
        daemon._key_callback(None, 5, True)
        assert item.done_event.is_set()
        assert item.response.status == "ok"
        assert item.response.chosen.label == "Allow"

    def test_deny_key(self, sample_request):
        daemon, item = self._setup(sample_request)
        daemon._key_callback(None, 3, True)
        assert item.done_event.is_set()
        assert item.response.chosen.label == "Deny"
        assert item.response.chosen.behavior == "deny"

    def test_key_release_ignored(self, sample_request):
        daemon, item = self._setup(sample_request)
        daemon._key_callback(None, 5, False)
        assert not item.done_event.is_set()

    def test_no_current_item_ignored(self):
        daemon = _make_ready_daemon()
        daemon._key_callback(None, 5, True)
        # No crash, no effect

    def test_message_key_ignored(self, sample_request):
        daemon, item = self._setup(sample_request)
        daemon._key_callback(None, 0, True)
        assert not item.done_event.is_set()

    def test_always_toggle_on(self, sample_request):
        daemon, item = self._setup(sample_request)
        daemon._key_callback(None, 4, True)
        assert not item.done_event.is_set()
        assert item.always_active is True

    def test_always_toggle_off(self, sample_request):
        daemon, item = self._setup(sample_request)
        item.always_active = True
        daemon._key_callback(None, 4, True)
        assert not item.done_event.is_set()
        assert item.always_active is False

    def test_always_then_allow(self, sample_request):
        daemon, item = self._setup(sample_request)
        daemon._key_callback(None, 4, True)
        assert item.always_active is True
        daemon._key_callback(None, 5, True)
        assert item.done_event.is_set()
        assert item.response.chosen.label == "Always"
        assert item.response.chosen.updated_permissions

    def test_deny_ignores_always(self, sample_request):
        daemon, item = self._setup(sample_request)
        item.always_active = True
        daemon._key_callback(None, 3, True)
        assert item.done_event.is_set()
        assert item.response.chosen.label == "Deny"
        assert item.response.chosen.behavior == "deny"

    def test_two_choice_allow(self, two_choice_request):
        daemon, item = self._setup(two_choice_request)
        daemon._key_callback(None, 5, True)
        assert item.response.chosen.label == "Allow"

    def test_two_choice_deny(self, two_choice_request):
        daemon, item = self._setup(two_choice_request)
        daemon._key_callback(None, 4, True)
        assert item.response.chosen.label == "Deny"


class TestDaemonWaitForResolution:
    def test_client_disconnect_removes_item(self, sample_request):
        daemon = _make_ready_daemon()
        item = _make_item(daemon, sample_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()
        client_sock.close()

        resp = daemon._wait_for_resolution(item, server_sock)
        server_sock.close()

        assert resp.status == "error"
        assert "disconnected" in resp.error_message.lower()
        assert item not in daemon._items

    def test_button_press_resolves(self, sample_request):
        daemon = _make_ready_daemon()
        item = _make_item(daemon, sample_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def press_allow():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 5, True)

        t = threading.Thread(target=press_allow)
        t.start()

        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.chosen.label == "Allow"


class TestDaemonFallback:
    def test_fallback_any_button_dismisses(self, exit_plan_mode_request):
        daemon = _make_ready_daemon()
        item = _make_item(
            daemon, exit_plan_mode_request,
            item_type="fallback", priority=PRIORITY_MEDIUM,
        )
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def press_button():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)

        t = threading.Thread(target=press_button)
        t.start()

        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "fallback"
        assert item not in daemon._items

    def test_fallback_key_release_ignored(self, exit_plan_mode_request):
        daemon = _make_ready_daemon()
        item = _make_item(
            daemon, exit_plan_mode_request,
            item_type="fallback", priority=PRIORITY_MEDIUM,
        )
        daemon._items.append(item)
        daemon._current_item = item

        daemon._key_callback(None, 0, False)
        assert not item.done_event.is_set()

    def test_fallback_client_disconnect(self, exit_plan_mode_request):
        daemon = _make_ready_daemon()
        item = _make_item(
            daemon, exit_plan_mode_request,
            item_type="fallback", priority=PRIORITY_MEDIUM,
        )
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()
        client_sock.close()

        resp = daemon._wait_for_resolution(item, server_sock)
        server_sock.close()

        assert resp.status == "error"
        assert "disconnected" in resp.error_message.lower()


class TestDaemonAskQuestion:
    def _make_ask_item(self, daemon, request):
        questions = request.tool_input.get("questions", [])
        item = _make_item(daemon, request, item_type="ask")
        item.ask_state = _AskQuestionState(
            questions=questions,
            total_pages=len(questions),
            current_page=0,
            answers={},
            multi_answers={},
            is_confirm_page=False,
        )
        return item

    def test_select_and_submit(self, ask_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.ask_answers == {"Which approach?": "Option A"}

    def test_cancel(self, ask_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 3, True)

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "error"
        assert "cancel" in resp.error_message.lower()

    def test_change_selection(self, ask_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)
            threading.Event().wait(0.2)
            daemon._key_callback(None, 1, True)
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.ask_answers == {"Which approach?": "Option B"}

    def test_submit_without_selection_ignored(self, ask_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 5, True)  # No selection yet
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.ask_answers == {"Which approach?": "Option A"}

    def test_multi_page_navigation(self, ask_multi_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_multi_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # A1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Next
            threading.Event().wait(0.2)
            daemon._key_callback(None, 0, True)  # B1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Confirm
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.ask_answers == {
            "First question?": "A1",
            "Second question?": "B1",
        }

    def test_multi_page_back(self, ask_multi_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_multi_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # A1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Next
            threading.Event().wait(0.2)
            daemon._key_callback(None, 3, True)  # Back
            threading.Event().wait(0.2)
            daemon._key_callback(None, 1, True)  # A2
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Next
            threading.Event().wait(0.2)
            daemon._key_callback(None, 0, True)  # B1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Confirm
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.ask_answers["First question?"] == "A2"

    def test_empty_key_ignored(self, ask_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 4, True)  # Empty
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.ask_answers == {"Which approach?": "Option A"}

    def test_confirm_page_empty_key_ignored(self, ask_multi_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_multi_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 0, True)  # A1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Next
            threading.Event().wait(0.2)
            daemon._key_callback(None, 0, True)  # B1
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Confirm
            threading.Event().wait(0.2)
            daemon._key_callback(None, 0, True)  # Empty - ignored
            threading.Event().wait(0.2)
            daemon._key_callback(None, 1, True)  # Empty - ignored
            threading.Event().wait(0.2)
            daemon._key_callback(None, 2, True)  # Empty - ignored
            threading.Event().wait(0.2)
            daemon._key_callback(None, 4, True)  # Empty - ignored
            threading.Event().wait(0.2)
            daemon._key_callback(None, 5, True)  # Submit

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "ok"
        assert resp.ask_answers == {
            "First question?": "A1",
            "Second question?": "B1",
        }

    def test_multi_page_cancel_on_first_page(self, ask_multi_question_request):
        daemon = _make_ready_daemon()
        item = self._make_ask_item(daemon, ask_multi_question_request)
        daemon._add_item(item)

        server_sock, client_sock = socket.socketpair()

        def interact():
            threading.Event().wait(0.3)
            daemon._key_callback(None, 3, True)

        t = threading.Thread(target=interact)
        t.start()
        resp = daemon._wait_for_resolution(item, server_sock)
        t.join()
        server_sock.close()
        client_sock.close()

        assert resp.status == "error"


class TestDaemonUnifiedQueue:
    """Tests for the unified display queue (add, remove, select, preempt)."""

    def test_add_item_displays(self, sample_request):
        daemon = _make_ready_daemon()
        item = _make_item(daemon, sample_request)
        daemon._add_item(item)

        assert daemon._current_item is item
        daemon.device_state.set_key_images.assert_called_once()

    def test_remove_item_clears_display(self, sample_request):
        daemon = _make_ready_daemon()
        item = _make_item(daemon, sample_request)
        daemon._add_item(item)
        daemon.device_state.set_key_images.reset_mock()

        daemon._remove_item(item)

        assert daemon._current_item is None
        daemon.device_state.clear_keys.assert_called_once()

    def test_same_pid_supersedes(self, sample_request):
        """New request from same PID supersedes the old one."""
        daemon = _make_ready_daemon()
        item_old = _make_item(daemon, sample_request, client_pid=1000)
        item_new = _make_item(daemon, sample_request, client_pid=1000)

        daemon._add_item(item_old)
        daemon._add_item(item_new)

        assert item_old not in daemon._items
        assert item_new in daemon._items
        assert item_old.done_event.is_set()
        assert item_old.response.status == "error"
        assert "superseded" in item_old.response.error_message.lower()
        assert daemon._current_item is item_new

    def test_higher_priority_displayed(self, sample_request, exit_plan_mode_request):
        """Higher priority item is displayed over lower priority."""
        import time

        daemon = _make_ready_daemon()

        # Add LOW notification first
        notif = _DisplayItem(
            id=daemon._next_id, priority=PRIORITY_LOW,
            timestamp=time.monotonic(), client_pid=2000,
            item_type="notification", notification_message="Idle",
            bg_color="#0A0A20",
        )
        daemon._next_id += 1
        daemon._add_item(notif)
        assert daemon._current_item is notif

        # Add HIGH permission request from different PID
        high = _make_item(daemon, sample_request, client_pid=1000)
        high.timestamp = time.monotonic()
        daemon._add_item(high)

        # HIGH should be displayed
        assert daemon._current_item is high

    def test_preempt_and_restore(self, sample_request):
        """Request A displayed, B arrives → B displayed. B resolved → A re-displayed."""
        import time

        daemon = _make_ready_daemon()

        item_a = _make_item(daemon, sample_request, client_pid=1000)
        item_a.timestamp = time.monotonic()
        daemon._add_item(item_a)
        assert daemon._current_item is item_a

        # B arrives from different PID, newer timestamp
        item_b = _make_item(daemon, sample_request, client_pid=2000)
        item_b.timestamp = time.monotonic()
        daemon._add_item(item_b)

        # B should be displayed (same priority, newer)
        assert daemon._current_item is item_b
        assert item_a in daemon._items  # A still in queue

        # B resolved (button press)
        daemon._key_callback(None, 5, True)

        # A should be re-displayed
        assert daemon._current_item is item_a

    def test_notification_dismissed_by_button(self):
        """OK button dismisses notification."""
        from cc_streamdeck.protocol import NotificationMessage

        daemon = _make_ready_daemon()

        msg = NotificationMessage(
            notification_type="idle_prompt", message="Idle", client_pid=1000,
        )
        daemon._handle_notification(msg)

        assert daemon._current_item is not None
        assert daemon._current_item.item_type == "notification"

        daemon._key_callback(None, 5, True)

        assert daemon._current_item is None
        assert len(daemon._items) == 0

    def test_notification_not_displayed_during_high(self, sample_request):
        """Notification stored but not displayed when HIGH is active."""
        import time

        from cc_streamdeck.protocol import NotificationMessage

        daemon = _make_ready_daemon()

        high = _make_item(daemon, sample_request, client_pid=1000)
        high.timestamp = time.monotonic()
        daemon._add_item(high)

        msg = NotificationMessage(
            notification_type="idle_prompt", message="Idle", client_pid=2000,
        )
        daemon._handle_notification(msg)

        # HIGH should still be displayed
        assert daemon._current_item is high
        assert len(daemon._items) == 2  # Both in queue

    def test_notification_displayed_after_high_resolved(self, sample_request):
        """After HIGH resolved, notification becomes visible."""
        import time

        from cc_streamdeck.protocol import NotificationMessage

        daemon = _make_ready_daemon()

        high = _make_item(daemon, sample_request, client_pid=1000)
        high.timestamp = time.monotonic()
        daemon._add_item(high)

        msg = NotificationMessage(
            notification_type="idle_prompt", message="Idle", client_pid=2000,
        )
        daemon._handle_notification(msg)

        # Resolve HIGH
        daemon._key_callback(None, 5, True)

        # Notification should now be displayed
        assert daemon._current_item is not None
        assert daemon._current_item.item_type == "notification"

    def test_same_pid_notification_superseded_by_high(self):
        """HIGH request from same PID supersedes its notification."""
        import time

        from cc_streamdeck.protocol import NotificationMessage

        daemon = _make_ready_daemon()

        msg = NotificationMessage(
            notification_type="idle_prompt", message="Idle", client_pid=1000,
        )
        daemon._handle_notification(msg)
        assert daemon._current_item.item_type == "notification"

        # HIGH from same PID
        from cc_streamdeck.protocol import PermissionChoice, PermissionRequest

        req = PermissionRequest(
            tool_name="Bash",
            tool_input={"command": "ls"},
            choices=[
                PermissionChoice(label="Allow", behavior="allow"),
                PermissionChoice(label="Deny", behavior="deny"),
            ],
            client_pid=1000,
        )
        high = _make_item(daemon, req, client_pid=1000)
        high.timestamp = time.monotonic()
        daemon._add_item(high)

        # Notification gone, HIGH displayed
        assert daemon._current_item is high
        notif_items = [i for i in daemon._items if i.item_type == "notification"]
        assert len(notif_items) == 0

    def test_disabled_notification_type_ignored(self):
        """Notification with disabled type is not added."""
        from cc_streamdeck.protocol import NotificationMessage

        daemon = _make_ready_daemon()
        daemon._settings.notification_types = ["idle_prompt"]

        msg = NotificationMessage(
            notification_type="auth_success", message="Auth", client_pid=1000,
        )
        daemon._handle_notification(msg)

        assert len(daemon._items) == 0
        assert daemon._current_item is None

    def test_notification_overwrites_same_pid(self):
        """New notification from same PID replaces old one."""
        from cc_streamdeck.protocol import NotificationMessage

        daemon = _make_ready_daemon()

        msg1 = NotificationMessage(
            notification_type="idle_prompt", message="First", client_pid=1000,
        )
        msg2 = NotificationMessage(
            notification_type="idle_prompt", message="Second", client_pid=1000,
        )
        daemon._handle_notification(msg1)
        daemon._handle_notification(msg2)

        assert len(daemon._items) == 1
        assert daemon._current_item.notification_message == "Second"

    def test_ask_preempt_preserves_state(self, ask_question_request, sample_request):
        """AskUserQuestion preempted mid-answer: state preserved on restore."""
        import time

        daemon = _make_ready_daemon()

        # Start ask question
        questions = ask_question_request.tool_input["questions"]
        ask_item = _make_item(daemon, ask_question_request, item_type="ask", client_pid=1000)
        ask_item.timestamp = time.monotonic()
        ask_item.ask_state = _AskQuestionState(
            questions=questions,
            total_pages=len(questions),
            current_page=0,
            answers={},
            multi_answers={},
            is_confirm_page=False,
        )
        daemon._add_item(ask_item)

        # User selects Option A
        daemon._key_callback(None, 0, True)
        assert ask_item.ask_state.answers.get(0) == "Option A"

        # HIGH from different PID preempts
        perm_item = _make_item(daemon, sample_request, client_pid=2000)
        perm_item.timestamp = time.monotonic()
        daemon._add_item(perm_item)
        assert daemon._current_item is perm_item

        # Ask state preserved
        assert ask_item.ask_state.answers.get(0) == "Option A"
        assert ask_item in daemon._items

        # Resolve permission
        daemon._key_callback(None, 5, True)

        # Ask restored with preserved state
        assert daemon._current_item is ask_item
        assert ask_item.ask_state.answers.get(0) == "Option A"
