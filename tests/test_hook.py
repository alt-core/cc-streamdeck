"""Tests for Hook Client logic."""

from unittest.mock import patch

from cc_streamdeck.hook import (
    _send_notification,
    _send_stop_hook,
    build_ask_question_output,
    build_hook_output,
    build_request,
)
from cc_streamdeck.protocol import PermissionChoice


class TestBuildRequest:
    def test_basic_request(self):
        hook_input = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "permission_suggestions": [],
        }
        req = build_request(hook_input)
        assert req.tool_name == "Bash"
        assert len(req.choices) == 2
        assert req.choices[0].label == "Allow"
        assert req.choices[1].label == "Deny"
        assert req.client_pid > 0

    def test_with_suggestions(self):
        hook_input = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "npm test"},
            "permission_suggestions": [{"type": "toolAlwaysAllow", "tool": "Bash"}],
        }
        req = build_request(hook_input)
        assert len(req.choices) == 3
        assert req.choices[2].label == "Always"
        assert req.choices[2].updated_permissions == [{"type": "toolAlwaysAllow", "tool": "Bash"}]

    def test_raw_hook_input_preserved(self):
        hook_input = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/test"},
            "permission_suggestions": [],
        }
        req = build_request(hook_input)
        assert req.raw_hook_input == hook_input

    def test_ask_question_request(self):
        hook_input = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "AskUserQuestion",
            "tool_input": {
                "questions": [
                    {
                        "question": "Which?",
                        "header": "Q",
                        "options": [{"label": "A", "description": ""}],
                        "multiSelect": False,
                    }
                ]
            },
            "permission_suggestions": [],
        }
        req = build_request(hook_input)
        assert req.tool_name == "AskUserQuestion"
        assert req.choices == []  # No pre-built choices
        assert req.tool_input["questions"][0]["question"] == "Which?"


class TestBuildHookOutput:
    def test_allow_output(self):
        chosen = PermissionChoice(label="Allow", behavior="allow")
        output = build_hook_output(chosen)
        decision = output["hookSpecificOutput"]["decision"]
        assert decision["behavior"] == "allow"
        assert "updatedPermissions" not in decision

    def test_deny_output(self):
        chosen = PermissionChoice(label="Deny", behavior="deny", message="Blocked")
        output = build_hook_output(chosen)
        decision = output["hookSpecificOutput"]["decision"]
        assert decision["behavior"] == "deny"
        assert decision["message"] == "Blocked"

    def test_always_allow_output(self):
        chosen = PermissionChoice(
            label="Always",
            behavior="allow",
            updated_permissions=[{"type": "toolAlwaysAllow", "tool": "Bash"}],
        )
        output = build_hook_output(chosen)
        decision = output["hookSpecificOutput"]["decision"]
        assert decision["behavior"] == "allow"
        assert decision["updatedPermissions"] == [{"type": "toolAlwaysAllow", "tool": "Bash"}]


class TestBuildAskQuestionOutput:
    def test_basic_output(self):
        hook_input = {
            "tool_input": {
                "questions": [
                    {"question": "Which?", "header": "Q", "options": [{"label": "A"}]}
                ]
            }
        }
        output = build_ask_question_output(hook_input, {"Which?": "A"})
        decision = output["hookSpecificOutput"]["decision"]
        assert decision["behavior"] == "allow"
        assert decision["updatedInput"]["answers"] == {"Which?": "A"}
        assert decision["updatedInput"]["questions"] == hook_input["tool_input"]["questions"]

    def test_multi_question_output(self):
        hook_input = {
            "tool_input": {
                "questions": [
                    {"question": "Q1?", "options": [{"label": "A"}]},
                    {"question": "Q2?", "options": [{"label": "B"}]},
                ]
            }
        }
        answers = {"Q1?": "A", "Q2?": "B"}
        output = build_ask_question_output(hook_input, answers)
        assert output["hookSpecificOutput"]["decision"]["updatedInput"]["answers"] == answers


class TestSendNotification:
    def test_sends_notification_message(self):
        """_send_notification creates correct NotificationMessage and sends it."""
        import socket

        hook_input = {
            "hook_event_name": "Notification",
            "notification_type": "idle_prompt",
            "message": "Claude is idle",
            "title": "Idle",
        }

        server, client = socket.socketpair()
        with patch("cc_streamdeck.hook._try_connect", return_value=client):
            with patch("cc_streamdeck.hook.os.getppid", return_value=99999):
                _send_notification(hook_input)

        data = b""
        while True:
            chunk = server.recv(4096)
            if not chunk:
                break
            data += chunk
        server.close()

        from cc_streamdeck.protocol import decode_notification

        msg = decode_notification(data)
        assert msg.notification_type == "idle_prompt"
        assert msg.message == "Claude is idle"
        assert msg.client_pid == 99999

    def test_no_daemon_silently_returns(self):
        """When no daemon is running, _send_notification returns without error."""
        with patch("cc_streamdeck.hook._try_connect", return_value=None):
            _send_notification({"notification_type": "idle_prompt", "message": "hi"})


class TestSendStopHook:
    def test_sends_stop_hook_message(self):
        """_send_stop_hook sends type=stop_hook with client_pid."""
        import json
        import socket

        server, client = socket.socketpair()
        with patch("cc_streamdeck.hook._try_connect", return_value=client):
            with patch("cc_streamdeck.hook.os.getppid", return_value=42000):
                _send_stop_hook()

        data = b""
        while True:
            chunk = server.recv(4096)
            if not chunk:
                break
            data += chunk
        server.close()

        msg = json.loads(data.decode("utf-8").strip())
        assert msg["type"] == "stop_hook"
        assert msg["client_pid"] == 42000

    def test_no_daemon_silently_returns(self):
        """When no daemon is running, _send_stop_hook returns without error."""
        with patch("cc_streamdeck.hook._try_connect", return_value=None):
            _send_stop_hook()
