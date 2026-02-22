"""Shared test fixtures."""

import pytest

from cc_streamdeck.protocol import PermissionChoice, PermissionRequest


@pytest.fixture
def sample_request():
    return PermissionRequest(
        tool_name="Bash",
        tool_input={"command": "rm -rf node_modules"},
        choices=[
            PermissionChoice(label="Allow", behavior="allow"),
            PermissionChoice(label="Deny", behavior="deny", message="Denied"),
            PermissionChoice(
                label="Always",
                behavior="allow",
                updated_permissions=[{"type": "toolAlwaysAllow", "tool": "Bash"}],
            ),
        ],
        raw_hook_input={"hook_event_name": "PermissionRequest", "tool_name": "Bash"},
    )


@pytest.fixture
def two_choice_request():
    return PermissionRequest(
        tool_name="Write",
        tool_input={"file_path": "/tmp/test.txt", "content": "hello"},
        choices=[
            PermissionChoice(label="Allow", behavior="allow"),
            PermissionChoice(label="Deny", behavior="deny", message="Denied"),
        ],
    )
