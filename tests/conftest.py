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
def exit_plan_mode_request():
    return PermissionRequest(
        tool_name="ExitPlanMode",
        tool_input={"allowedPrompts": [{"tool": "Bash", "prompt": "run tests"}]},
        choices=[
            PermissionChoice(label="Allow", behavior="allow"),
            PermissionChoice(label="Deny", behavior="deny", message="Denied"),
        ],
        raw_hook_input={"hook_event_name": "PermissionRequest", "tool_name": "ExitPlanMode"},
    )


@pytest.fixture
def ask_question_request():
    return PermissionRequest(
        tool_name="AskUserQuestion",
        tool_input={
            "questions": [
                {
                    "question": "Which approach?",
                    "header": "Approach",
                    "options": [
                        {"label": "Option A", "description": "First approach"},
                        {"label": "Option B", "description": "Second approach"},
                        {"label": "Option C", "description": "Third approach"},
                    ],
                    "multiSelect": False,
                }
            ]
        },
        choices=[],
        raw_hook_input={"hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion"},
    )


@pytest.fixture
def ask_multi_question_request():
    return PermissionRequest(
        tool_name="AskUserQuestion",
        tool_input={
            "questions": [
                {
                    "question": "First question?",
                    "header": "Q1",
                    "options": [
                        {"label": "A1", "description": ""},
                        {"label": "A2", "description": ""},
                    ],
                    "multiSelect": False,
                },
                {
                    "question": "Second question?",
                    "header": "Q2",
                    "options": [
                        {"label": "B1", "description": ""},
                        {"label": "B2", "description": ""},
                    ],
                    "multiSelect": False,
                },
            ]
        },
        choices=[],
        raw_hook_input={"hook_event_name": "PermissionRequest", "tool_name": "AskUserQuestion"},
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
