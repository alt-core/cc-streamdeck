"""IPC message types and NDJSON serialization for Unix domain socket communication."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class PermissionChoice:
    """A single choice the user can make on the Stream Deck."""

    label: str
    behavior: Literal["allow", "deny"]
    updated_permissions: list[dict] = field(default_factory=list)
    message: str = ""


@dataclass
class PermissionRequest:
    """Sent from Hook Client to Daemon."""

    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    choices: list[PermissionChoice] = field(default_factory=list)
    raw_hook_input: dict = field(default_factory=dict)
    client_pid: int = 0
    type: Literal["permission_request"] = "permission_request"


@dataclass
class PermissionResponse:
    """Sent from Daemon to Hook Client."""

    status: Literal["ok", "no_device", "error", "fallback"] = "ok"
    chosen: PermissionChoice | None = None
    error_message: str = ""
    ask_answers: dict = field(default_factory=dict)
    type: Literal["permission_response"] = "permission_response"


@dataclass
class NotificationMessage:
    """Sent from Hook Client to Daemon for low-priority notifications."""

    notification_type: str = ""  # "idle_prompt", "auth_success", etc.
    message: str = ""
    title: str = ""
    client_pid: int = 0
    type: Literal["notification"] = "notification"


def encode(msg: PermissionRequest | PermissionResponse | NotificationMessage) -> bytes:
    """Serialize a dataclass message to NDJSON bytes."""
    return (json.dumps(asdict(msg), ensure_ascii=False) + "\n").encode("utf-8")


def decode_request(data: bytes) -> PermissionRequest:
    """Deserialize NDJSON bytes to a PermissionRequest."""
    obj = json.loads(data.decode("utf-8").strip())
    choices = [PermissionChoice(**c) for c in obj.get("choices", [])]
    return PermissionRequest(
        tool_name=obj.get("tool_name", ""),
        tool_input=obj.get("tool_input", {}),
        choices=choices,
        raw_hook_input=obj.get("raw_hook_input", {}),
        client_pid=obj.get("client_pid", 0),
        type=obj.get("type", "permission_request"),
    )


def decode_notification(data: bytes) -> NotificationMessage:
    """Deserialize NDJSON bytes to a NotificationMessage."""
    obj = json.loads(data.decode("utf-8").strip())
    return NotificationMessage(
        notification_type=obj.get("notification_type", ""),
        message=obj.get("message", ""),
        title=obj.get("title", ""),
        client_pid=obj.get("client_pid", 0),
        type=obj.get("type", "notification"),
    )


def decode_response(data: bytes) -> PermissionResponse:
    """Deserialize NDJSON bytes to a PermissionResponse."""
    obj = json.loads(data.decode("utf-8").strip())
    chosen = PermissionChoice(**obj["chosen"]) if obj.get("chosen") else None
    return PermissionResponse(
        status=obj.get("status", "ok"),
        chosen=chosen,
        error_message=obj.get("error_message", ""),
        ask_answers=obj.get("ask_answers", {}),
        type=obj.get("type", "permission_response"),
    )
