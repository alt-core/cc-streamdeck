"""Tests for Hook Client logic."""

from cc_streamdeck.hook import build_hook_output, build_request
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
