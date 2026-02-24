"""Focus the terminal running Claude Code (macOS).

Walks the process tree from a given PID to find the terminal emulator,
then activates it with the correct tab/pane via AppleScript or CLI tools.

Layers (executed inside-out):
1. tmux pane selection — select the right pane if running inside tmux
2. Terminal tab selection — select the tab by TTY matching (iTerm2, Terminal.app, WezTerm)
3. App activation — bring the terminal app to the foreground via osascript
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# Known terminal process names → AppleScript application names
TERMINAL_APPS: dict[str, str] = {
    "Terminal": "Terminal",
    "iTerm2": "iTerm2",
    "iTerm.app": "iTerm2",
    "Ghostty": "Ghostty",
    "ghostty": "Ghostty",
    "Alacritty": "Alacritty",
    "alacritty": "Alacritty",
    "kitty": "kitty",
    "WezTerm": "WezTerm",
    "wezterm-gui": "WezTerm",
    "Claude": "Claude",
}


def _get_process_info(pid: int) -> tuple[int, str] | None:
    """Return (ppid, comm) for a PID using ps."""
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2.0,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.strip()
        parts = line.split(None, 1)
        if len(parts) < 2:
            return None
        return int(parts[0]), parts[1].strip()
    except Exception:
        return None


def _get_tty(pid: int) -> str:
    """Return the TTY name for a PID (e.g. 'ttys001'), or empty string."""
    try:
        result = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2.0,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _walk_ancestors(pid: int) -> list[tuple[int, str]]:
    """Walk the process tree upward, returning [(pid, comm), ...]."""
    ancestors: list[tuple[int, str]] = []
    current = pid
    seen: set[int] = set()
    while current > 1 and current not in seen and len(ancestors) < 20:
        seen.add(current)
        info = _get_process_info(current)
        if info is None:
            break
        ppid, comm = info
        ancestors.append((current, comm))
        current = ppid
    return ancestors


def _find_terminal_app(ancestors: list[tuple[int, str]]) -> str | None:
    """Find the terminal application from the ancestor chain."""
    for _, comm in ancestors:
        # comm may be a full path like /Applications/iTerm.app/...
        basename = comm.rsplit("/", 1)[-1]
        for pattern, app_name in TERMINAL_APPS.items():
            if pattern in basename:
                return app_name
    return None


def _is_descendant(pid: int, ancestor_pid: int) -> bool:
    """Check if pid is a descendant of ancestor_pid."""
    current = pid
    seen: set[int] = set()
    while current > 1 and current not in seen:
        if current == ancestor_pid:
            return True
        seen.add(current)
        info = _get_process_info(current)
        if info is None:
            break
        current = info[0]
    return False


def _try_tmux_focus(client_pid: int) -> tuple[str, str] | None:
    """If running inside tmux, select the right pane.

    Returns (terminal_app, client_tty) by walking the tmux client's
    ancestor chain, or None if not running in tmux.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{pane_pid} #{pane_id} #{session_name}:#{window_index}"],
            capture_output=True, text=True, timeout=2.0,
        )
        if result.returncode != 0:
            return None
    except FileNotFoundError:
        return None
    except Exception:
        return None

    session_name = None
    for line in result.stdout.strip().split("\n"):
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pane_pid = int(parts[0])
        except ValueError:
            continue
        pane_id, target = parts[1], parts[2]

        if _is_descendant(client_pid, pane_pid):
            subprocess.run(
                ["tmux", "select-window", "-t", target],
                capture_output=True, timeout=2.0,
            )
            subprocess.run(
                ["tmux", "select-pane", "-t", pane_id],
                capture_output=True, timeout=2.0,
            )
            session_name = target.split(":")[0]
            break

    if session_name is None:
        return None

    # Find the tmux client attached to this session.
    # The client process (not the server) is a child of the terminal app,
    # so walking its ancestors reveals which terminal we're in.
    try:
        result = subprocess.run(
            ["tmux", "list-clients", "-t", session_name,
             "-F", "#{client_pid}"],
            capture_output=True, text=True, timeout=2.0,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    tmux_client_pid = int(line)
                except ValueError:
                    continue
                ancestors = _walk_ancestors(tmux_client_pid)
                app = _find_terminal_app(ancestors)
                tty = _get_tty(tmux_client_pid)
                if app:
                    return app, tty
    except Exception:
        pass

    return None


def _try_tab_focus(app_name: str, tty: str) -> bool:
    """Try to select the terminal tab containing the given TTY. Returns True if successful."""
    if not tty or tty == "??":
        return False

    if app_name == "iTerm2":
        return _try_iterm2_tab(tty)
    elif app_name == "Terminal":
        return _try_terminal_tab(tty)
    elif app_name == "WezTerm":
        return _try_wezterm_tab(tty)
    return False


def _try_iterm2_tab(tty: str) -> bool:
    """Select the iTerm2 tab+session matching the given TTY."""
    script = f'''
tell application "iTerm2"
    repeat with w in windows
        set tabIdx to 0
        repeat with t in tabs of w
            set tabIdx to tabIdx + 1
            repeat with s in sessions of t
                if tty of s contains "{tty}" then
                    tell w to select tab tabIdx
                    select s
                    set index of w to 1
                    return true
                end if
            end repeat
        end repeat
    end repeat
end tell
return false
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3.0,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _try_terminal_tab(tty: str) -> bool:
    """Select the Terminal.app tab matching the given TTY."""
    script = f'''
tell application "Terminal"
    repeat with w in windows
        repeat with t in tabs of w
            if tty of t contains "{tty}" then
                set selected tab of w to t
                set index of w to 1
                return true
            end if
        end repeat
    end repeat
end tell
return false
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3.0,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _try_wezterm_tab(tty: str) -> bool:
    """Select the WezTerm pane matching the given TTY."""
    try:
        result = subprocess.run(
            ["wezterm", "cli", "list", "--format", "json"],
            capture_output=True, text=True, timeout=2.0,
        )
        if result.returncode != 0:
            return False
        panes = json.loads(result.stdout)
        for pane in panes:
            pane_tty = pane.get("tty_name", "")
            if tty in pane_tty or pane_tty.endswith(tty):
                pane_id = pane.get("pane_id")
                if pane_id is not None:
                    subprocess.run(
                        ["wezterm", "cli", "activate-pane", "--pane-id", str(pane_id)],
                        capture_output=True, timeout=2.0,
                    )
                    return True
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception:
        pass
    return False


def _activate_app(app_name: str) -> bool:
    """Activate a macOS application via osascript."""
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            capture_output=True, timeout=3.0,
        )
        return True
    except Exception:
        return False


def focus_pid(client_pid: int) -> None:
    """Focus the terminal running the given PID (library entry point)."""
    ancestors = _walk_ancestors(client_pid)
    tty = _get_tty(client_pid)
    logger.debug("focus_pid(%d): tty=%r, ancestors=%s", client_pid, tty, ancestors)

    # Layer 1: tmux pane selection (also resolves terminal app via client)
    tmux_result = _try_tmux_focus(client_pid)

    if tmux_result:
        app, client_tty = tmux_result
        # Layer 2: tab selection using tmux client's TTY
        if client_tty:
            _try_tab_focus(app, client_tty)
        # Layer 3: app activation
        _activate_app(app)
    else:
        # Non-tmux: use direct ancestor chain
        app = _find_terminal_app(ancestors)
        if app and tty:
            _try_tab_focus(app, tty)
        _activate_app(app or "Terminal")


def main() -> None:
    """Focus the terminal running Claude Code (CLI entry point)."""
    if len(sys.argv) < 2:
        print("Usage: cc-streamdeck-focus <client_pid>", file=sys.stderr)
        sys.exit(1)

    try:
        client_pid = int(sys.argv[1])
    except ValueError:
        print(f"Invalid PID: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    focus_pid(client_pid)
