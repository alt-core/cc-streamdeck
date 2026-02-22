"""Hook Client: invoked by Claude Code's PermissionRequest hook.

Reads JSON from stdin, communicates with the Daemon via Unix socket,
and writes the response JSON to stdout. Falls back to terminal prompt
on any error (exit 0 with no output).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time

from .config import CONNECT_RETRY_INTERVAL, DAEMON_STARTUP_TIMEOUT, SOCKET_PATH
from .protocol import (
    PermissionChoice,
    PermissionRequest,
    PermissionResponse,
    decode_response,
    encode,
)


def build_request(hook_input: dict) -> PermissionRequest:
    """Convert Claude Code hook input to internal PermissionRequest."""
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    suggestions = hook_input.get("permission_suggestions", [])

    choices = [
        PermissionChoice(label="Allow", behavior="allow"),
        PermissionChoice(label="Deny", behavior="deny", message="Denied via Stream Deck"),
    ]

    # Add "Always" choice from the first suggestion
    for suggestion in suggestions[:1]:
        choices.append(
            PermissionChoice(
                label="Always",
                behavior="allow",
                updated_permissions=[suggestion],
            )
        )

    return PermissionRequest(
        tool_name=tool_name,
        tool_input=tool_input,
        choices=choices,
        raw_hook_input=hook_input,
    )


def build_hook_output(chosen: PermissionChoice) -> dict:
    """Build the stdout JSON response for Claude Code."""
    decision: dict = {"behavior": chosen.behavior}

    if chosen.updated_permissions:
        decision["updatedPermissions"] = chosen.updated_permissions

    if chosen.behavior == "deny" and chosen.message:
        decision["message"] = chosen.message

    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }


def _try_connect() -> socket.socket | None:
    """Attempt a single connection to the daemon socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(600.0)
        sock.connect(str(SOCKET_PATH))
        return sock
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return None


def _start_daemon() -> None:
    """Start the daemon process in the background.

    Resolves cc-streamdeck-daemon from the same directory as this script,
    so it works even when the .venv/bin is not on PATH.
    """
    import shutil
    from pathlib import Path

    # Look for daemon next to the running hook script
    hook_dir = Path(sys.executable).parent
    daemon_path = hook_dir / "cc-streamdeck-daemon"
    if not daemon_path.exists():
        daemon_path = shutil.which("cc-streamdeck-daemon") or "cc-streamdeck-daemon"

    subprocess.Popen(
        [str(daemon_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def connect_to_daemon() -> socket.socket | None:
    """Connect to daemon, auto-starting if necessary."""
    sock = _try_connect()
    if sock is not None:
        return sock

    _start_daemon()

    deadline = time.monotonic() + DAEMON_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(CONNECT_RETRY_INTERVAL)
        sock = _try_connect()
        if sock is not None:
            return sock

    return None


def _communicate(sock: socket.socket, request: PermissionRequest) -> PermissionResponse:
    """Send request and receive response from the daemon."""
    sock.sendall(encode(request))
    sock.shutdown(socket.SHUT_WR)

    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk

    return decode_response(data)


def main() -> None:
    try:
        raw_input = sys.stdin.read()
        hook_input = json.loads(raw_input)

        request = build_request(hook_input)

        sock = connect_to_daemon()
        if sock is None:
            sys.exit(0)

        try:
            response = _communicate(sock, request)
        finally:
            sock.close()

        if response.status != "ok" or response.chosen is None:
            sys.exit(0)

        output = build_hook_output(response.chosen)
        sys.stdout.write(json.dumps(output, ensure_ascii=False))
        sys.exit(0)

    except Exception:
        sys.exit(0)
