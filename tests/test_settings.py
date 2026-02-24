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


class TestParseBashRules:
    """Test parsing of named bash rule settings."""

    def test_bash_levels(self):
        data = {"risk": {"bash": {"levels": {"curl": "low", "wget": "medium"}}}}
        settings = _parse(data)
        assert settings.bash_levels == {"curl": "low", "wget": "medium"}

    def test_bash_prepend(self):
        data = {
            "risk": {
                "bash": {
                    "prepend": [
                        {"name": "my-rule", "pattern": "my-cmd", "level": "low"},
                        {"name": "other", "pattern": "other-cmd"},  # no level -> defaults
                    ]
                }
            }
        }
        settings = _parse(data)
        assert len(settings.bash_prepend) == 2
        assert settings.bash_prepend[0]["name"] == "my-rule"
        assert settings.bash_prepend[0]["pattern"] == "my-cmd"
        assert settings.bash_prepend[0]["level"] == "low"
        assert settings.bash_prepend[1]["name"] == "other"
        assert "level" not in settings.bash_prepend[1]

    def test_bash_append(self):
        data = {
            "risk": {
                "bash": {
                    "append": [
                        {"name": "custom", "pattern": "custom-tool", "level": "high"},
                    ]
                }
            }
        }
        settings = _parse(data)
        assert len(settings.bash_append) == 1
        assert settings.bash_append[0]["name"] == "custom"

    def test_bash_prepend_skips_invalid(self):
        data = {
            "risk": {
                "bash": {
                    "prepend": [
                        {"name": "no-pattern"},  # missing pattern
                        {"pattern": "no-name", "level": "low"},  # missing name
                        "not-a-dict",  # not a dict
                        {"name": "valid", "pattern": "cmd", "level": "low"},
                    ]
                }
            }
        }
        settings = _parse(data)
        assert len(settings.bash_prepend) == 1
        assert settings.bash_prepend[0]["name"] == "valid"

    def test_empty_bash_section(self):
        data = {"risk": {"bash": {}}}
        settings = _parse(data)
        assert settings.bash_levels == {}
        assert settings.bash_prepend == []
        assert settings.bash_append == []


class TestParseNotification:
    """Test parsing of notification settings."""

    def test_default_notification_types(self):
        settings = _parse({})
        assert "idle_prompt" in settings.notification_types
        assert "auth_success" in settings.notification_types
        assert "elicitation_dialog" in settings.notification_types
        assert "stop" in settings.notification_types

    def test_custom_notification_types(self):
        data = {"notification": {"types": ["idle_prompt"]}}
        settings = _parse(data)
        assert settings.notification_types == ["idle_prompt"]

    def test_empty_notification_types(self):
        data = {"notification": {"types": []}}
        settings = _parse(data)
        assert settings.notification_types == []

    def test_no_notification_section(self):
        data = {"colors": {"risk": {}}}
        settings = _parse(data)
        # Defaults preserved when no notification section
        assert len(settings.notification_types) == 4


class TestDisplayGuardSettings:
    def test_default_guard_ms(self):
        settings = _parse({})
        assert settings.display_guard_ms == 500

    def test_default_minor_guard_ms(self):
        settings = _parse({})
        assert settings.display_minor_guard_ms == 0

    def test_custom_guard_ms(self):
        data = {"display": {"guard_ms": 300}}
        settings = _parse(data)
        assert settings.display_guard_ms == 300

    def test_custom_minor_guard_ms(self):
        data = {"display": {"minor_guard_ms": 500}}
        settings = _parse(data)
        assert settings.display_minor_guard_ms == 500

    def test_negative_guard_ms_clamped(self):
        data = {"display": {"guard_ms": -100}}
        settings = _parse(data)
        assert settings.display_guard_ms == 0

    def test_negative_minor_guard_ms_clamped(self):
        data = {"display": {"minor_guard_ms": -50}}
        settings = _parse(data)
        assert settings.display_minor_guard_ms == 0

    def test_both_guard_settings(self):
        data = {"display": {"guard_ms": 500, "minor_guard_ms": 200}}
        settings = _parse(data)
        assert settings.display_guard_ms == 500
        assert settings.display_minor_guard_ms == 200

    def test_default_guard_dim_off(self):
        settings = _parse({})
        assert settings.display_guard_dim is False

    def test_guard_dim_on(self):
        data = {"display": {"guard_dim": True}}
        settings = _parse(data)
        assert settings.display_guard_dim is True

