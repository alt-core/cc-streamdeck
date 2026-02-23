"""User configuration loading from TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def get_config_path() -> Path:
    """Return the config file path (XDG Base Directory compliant)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "cc-streamdeck" / "config.toml"


@dataclass
class UserSettings:
    """Parsed user settings from config.toml."""

    # Risk level colors (header)
    risk_colors: dict[str, dict[str, str]] = field(default_factory=dict)
    # Instance identification colors (body background)
    instance_palette: list[str] = field(default_factory=list)
    # Body text color
    body_text_color: str = ""
    # Tool risk level overrides
    tool_risk: dict[str, str] = field(default_factory=dict)
    tool_risk_default: str = ""
    # Named bash rule system
    bash_prepend: list[dict[str, str]] = field(default_factory=list)
    bash_append: list[dict[str, str]] = field(default_factory=list)
    bash_levels: dict[str, str] = field(default_factory=dict)
    # Path patterns for Write/Edit risk elevation
    path_critical: list[str] = field(default_factory=list)
    path_high: list[str] = field(default_factory=list)
    # Notification types to display (empty = all enabled)
    notification_types: list[str] = field(
        default_factory=lambda: ["idle_prompt", "auth_success", "elicitation_dialog"]
    )


def load_settings() -> UserSettings:
    """Load settings from config file. Returns defaults if file missing or malformed."""
    path = get_config_path()
    if not path.exists():
        return UserSettings()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return _parse(data)
    except Exception:
        return UserSettings()


def _parse(data: dict) -> UserSettings:
    """Parse TOML dict into UserSettings."""
    settings = UserSettings()

    # [colors.risk]
    colors_risk = data.get("colors", {}).get("risk", {})
    for level in ("critical", "high", "medium", "low"):
        bg = colors_risk.get(f"{level}_bg")
        fg = colors_risk.get(f"{level}_fg")
        if bg or fg:
            settings.risk_colors[level] = {}
            if bg:
                settings.risk_colors[level]["bg"] = bg
            if fg:
                settings.risk_colors[level]["fg"] = fg

    # [colors.instance]
    palette = data.get("colors", {}).get("instance", {}).get("palette")
    if isinstance(palette, list):
        settings.instance_palette = [str(c) for c in palette]

    # [colors.body]
    body_text = data.get("colors", {}).get("body", {}).get("text")
    if body_text:
        settings.body_text_color = str(body_text)

    # [risk.tools]
    risk_tools = data.get("risk", {}).get("tools", {})
    for k, v in risk_tools.items():
        if k == "default":
            settings.tool_risk_default = str(v)
        else:
            settings.tool_risk[k] = str(v)

    # [risk.bash.levels]
    bash_levels = data.get("risk", {}).get("bash", {}).get("levels", {})
    if isinstance(bash_levels, dict):
        settings.bash_levels = {str(k): str(v) for k, v in bash_levels.items()}

    # [[risk.bash.prepend]]
    bash_prepend = data.get("risk", {}).get("bash", {}).get("prepend", [])
    if isinstance(bash_prepend, list):
        for entry in bash_prepend:
            if isinstance(entry, dict):
                rule = {}
                for key in ("name", "pattern", "level"):
                    if key in entry:
                        rule[key] = str(entry[key])
                if "name" in rule and "pattern" in rule:
                    settings.bash_prepend.append(rule)

    # [[risk.bash.append]]
    bash_append = data.get("risk", {}).get("bash", {}).get("append", [])
    if isinstance(bash_append, list):
        for entry in bash_append:
            if isinstance(entry, dict):
                rule = {}
                for key in ("name", "pattern", "level"):
                    if key in entry:
                        rule[key] = str(entry[key])
                if "name" in rule and "pattern" in rule:
                    settings.bash_append.append(rule)

    # [notification]
    notif_types = data.get("notification", {}).get("types")
    if isinstance(notif_types, list):
        settings.notification_types = [str(t) for t in notif_types]

    # [risk.path_*]
    for level, attr in [
        ("path_critical", "path_critical"),
        ("path_high", "path_high"),
    ]:
        patterns = data.get("risk", {}).get(level, {}).get("patterns", [])
        if isinstance(patterns, list):
            setattr(settings, attr, [str(p) for p in patterns])

    return settings
