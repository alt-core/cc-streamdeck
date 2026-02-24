"""Tests for focus module (cc-streamdeck-focus command)."""

from unittest.mock import MagicMock, patch

from cc_streamdeck.focus import (
    TERMINAL_APPS,
    _activate_app,
    _find_terminal_app,
    _get_process_info,
    _get_tty,
    _is_descendant,
    _try_tmux_focus,
    _walk_ancestors,
)


class TestGetProcessInfo:
    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_ppid_and_comm(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  100 zsh\n")
        result = _get_process_info(200)
        assert result == (100, "zsh")

    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _get_process_info(999999) is None

    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_none_on_exception(self, mock_run):
        mock_run.side_effect = Exception("ps failed")
        assert _get_process_info(200) is None


class TestGetTty:
    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_tty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ttys001\n")
        assert _get_tty(200) == "ttys001"

    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _get_tty(999999) == ""


class TestWalkAncestors:
    @patch("cc_streamdeck.focus._get_process_info")
    def test_walks_chain(self, mock_info):
        # 300 → 200 → 100 → 1
        mock_info.side_effect = lambda pid: {
            300: (200, "claude"),
            200: (100, "zsh"),
            100: (1, "Terminal"),
        }.get(pid)
        result = _walk_ancestors(300)
        assert len(result) == 3
        assert result[0] == (300, "claude")
        assert result[1] == (200, "zsh")
        assert result[2] == (100, "Terminal")

    @patch("cc_streamdeck.focus._get_process_info")
    def test_stops_on_none(self, mock_info):
        mock_info.side_effect = lambda pid: (200, "zsh") if pid == 300 else None
        result = _walk_ancestors(300)
        assert len(result) == 1

    @patch("cc_streamdeck.focus._get_process_info")
    def test_stops_on_cycle(self, mock_info):
        mock_info.return_value = (300, "loop")  # PID points to itself
        result = _walk_ancestors(300)
        assert len(result) == 1


class TestFindTerminalApp:
    def test_finds_iterm2(self):
        ancestors = [(300, "claude"), (200, "zsh"), (100, "iTerm2")]
        assert _find_terminal_app(ancestors) == "iTerm2"

    def test_finds_terminal(self):
        ancestors = [(300, "claude"), (200, "zsh"), (100, "Terminal")]
        assert _find_terminal_app(ancestors) == "Terminal"

    def test_finds_ghostty(self):
        ancestors = [(300, "claude"), (100, "Ghostty")]
        assert _find_terminal_app(ancestors) == "Ghostty"

    def test_finds_wezterm(self):
        ancestors = [(300, "claude"), (100, "wezterm-gui")]
        assert _find_terminal_app(ancestors) == "WezTerm"

    def test_finds_claude_desktop(self):
        ancestors = [(300, "node"), (100, "Claude")]
        assert _find_terminal_app(ancestors) == "Claude"

    def test_returns_none_for_unknown(self):
        ancestors = [(300, "claude"), (200, "bash"), (100, "init")]
        assert _find_terminal_app(ancestors) is None

    def test_handles_full_path(self):
        ancestors = [(100, "/Applications/iTerm.app/Contents/MacOS/iTerm2")]
        assert _find_terminal_app(ancestors) == "iTerm2"


class TestIsDescendant:
    @patch("cc_streamdeck.focus._get_process_info")
    def test_true_when_descendant(self, mock_info):
        mock_info.side_effect = lambda pid: {
            300: (200, "claude"),
            200: (100, "zsh"),
        }.get(pid)
        assert _is_descendant(300, 100)

    @patch("cc_streamdeck.focus._get_process_info")
    def test_true_when_same(self, mock_info):
        assert _is_descendant(100, 100)

    @patch("cc_streamdeck.focus._get_process_info")
    def test_false_when_unrelated(self, mock_info):
        mock_info.side_effect = lambda pid: {
            300: (200, "claude"),
            200: (1, "init"),
        }.get(pid)
        assert not _is_descendant(300, 500)


class TestTryTmuxFocus:
    @patch("cc_streamdeck.focus._get_tty")
    @patch("cc_streamdeck.focus._walk_ancestors")
    @patch("cc_streamdeck.focus._find_terminal_app")
    @patch("cc_streamdeck.focus.subprocess.run")
    @patch("cc_streamdeck.focus._is_descendant")
    def test_selects_matching_pane_and_finds_terminal(
        self, mock_desc, mock_run, mock_find_app, mock_walk, mock_tty,
    ):
        list_panes = MagicMock(returncode=0, stdout="100 %0 main:0\n200 %1 main:1\n")
        list_clients = MagicMock(returncode=0, stdout="500\n")
        mock_run.side_effect = [
            list_panes,                   # list-panes
            MagicMock(returncode=0),      # select-window
            MagicMock(returncode=0),      # select-pane
            list_clients,                 # list-clients
        ]
        mock_desc.side_effect = lambda pid, ancestor: pid == 300 and ancestor == 200
        mock_walk.return_value = [(500, "tmux"), (400, "zsh"), (300, "iTerm2")]
        mock_find_app.return_value = "iTerm2"
        mock_tty.return_value = "ttys003"

        result = _try_tmux_focus(300)
        assert result == ("iTerm2", "ttys003")

    @patch("cc_streamdeck.focus.subprocess.run")
    @patch("cc_streamdeck.focus._is_descendant")
    def test_returns_none_when_no_matching_pane(self, mock_desc, mock_run):
        list_panes = MagicMock(returncode=0, stdout="100 %0 main:0\n")
        mock_run.return_value = list_panes
        mock_desc.return_value = False

        assert _try_tmux_focus(300) is None

    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_none_when_tmux_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError
        assert _try_tmux_focus(300) is None

    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_none_when_no_tmux_server(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _try_tmux_focus(300) is None


class TestActivateApp:
    @patch("cc_streamdeck.focus.subprocess.run")
    def test_calls_osascript(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert _activate_app("Terminal")
        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"
        assert "Terminal" in args[2]

    @patch("cc_streamdeck.focus.subprocess.run")
    def test_returns_false_on_error(self, mock_run):
        mock_run.side_effect = Exception("osascript failed")
        assert not _activate_app("Terminal")


class TestSanitizeTty:
    def test_normal_tty_unchanged(self):
        from cc_streamdeck.focus import _sanitize_tty

        assert _sanitize_tty("ttys001") == "ttys001"

    def test_strips_quotes(self):
        from cc_streamdeck.focus import _sanitize_tty

        assert _sanitize_tty('tty"injection') == "ttyinjection"

    def test_strips_backslashes(self):
        from cc_streamdeck.focus import _sanitize_tty

        assert _sanitize_tty("tty\\s001") == "ttys001"

    def test_empty_after_sanitize_blocks_tab_focus(self):
        from cc_streamdeck.focus import _try_tab_focus

        assert not _try_tab_focus("iTerm2", '"\\"')


class TestTerminalAppsMapping:
    def test_all_common_terminals_present(self):
        expected = {"Terminal", "iTerm2", "Ghostty", "WezTerm", "Claude"}
        actual_apps = set(TERMINAL_APPS.values())
        for app in expected:
            assert app in actual_apps
