"""Tests for settings module."""

from cc_streamdeck.settings import UserSettings, _parse, get_config_path, load_settings


class TestGetConfigPath:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        path = get_config_path()
        assert path.name == "config.toml"
        assert "cc-streamdeck" in str(path)
        assert ".config" in str(path)

    def test_xdg_override(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/config")
        path = get_config_path()
        assert str(path).startswith("/custom/config")


class TestLoadSettings:
    def test_missing_file_returns_defaults(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent/path")
        settings = load_settings()
        assert isinstance(settings, UserSettings)
        assert settings.risk_colors == {}
        assert settings.instance_palette == []
        assert settings.body_text_color == ""


class TestParse:
    def test_empty_dict(self):
        settings = _parse({})
        assert isinstance(settings, UserSettings)

    def test_risk_colors(self):
        data = {
            "colors": {
                "risk": {
                    "critical_bg": "#FF0000",
                    "critical_fg": "#FFFFFF",
                    "low_bg": "#000000",
                }
            }
        }
        settings = _parse(data)
        assert settings.risk_colors["critical"]["bg"] == "#FF0000"
        assert settings.risk_colors["critical"]["fg"] == "#FFFFFF"
        assert settings.risk_colors["low"]["bg"] == "#000000"
        assert "fg" not in settings.risk_colors["low"]

    def test_instance_palette(self):
        data = {"colors": {"instance": {"palette": ["#111", "#222", "#333"]}}}
        settings = _parse(data)
        assert settings.instance_palette == ["#111", "#222", "#333"]

    def test_body_text(self):
        data = {"colors": {"body": {"text": "#CCCCCC"}}}
        settings = _parse(data)
        assert settings.body_text_color == "#CCCCCC"

    def test_tool_risk(self):
        data = {
            "risk": {
                "tools": {
                    "Bash": "evaluate",
                    "Write": "critical",
                    "default": "high",
                }
            }
        }
        settings = _parse(data)
        assert settings.tool_risk["Bash"] == "evaluate"
        assert settings.tool_risk["Write"] == "critical"
        assert settings.tool_risk_default == "high"

    def test_bash_patterns(self):
        data = {
            "risk": {
                "bash_critical": {"patterns": [r"\bmy-danger\b"]},
                "bash_low": {"patterns": [r"^\s*my-safe\b"]},
            }
        }
        settings = _parse(data)
        assert settings.bash_critical_extra == [r"\bmy-danger\b"]
        assert settings.bash_low_extra == [r"^\s*my-safe\b"]

    def test_path_patterns(self):
        data = {
            "risk": {
                "path_critical": {"patterns": [r"\.env$"]},
                "path_high": {"patterns": [r"/etc/"]},
            }
        }
        settings = _parse(data)
        assert settings.path_critical == [r"\.env$"]
        assert settings.path_high == [r"/etc/"]
