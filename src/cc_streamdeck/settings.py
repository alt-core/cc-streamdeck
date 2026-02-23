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
    # Extra Bash patterns (added to built-in)
    bash_critical_extra: list[str] = field(default_factory=list)
    bash_high_extra: list[str] = field(default_factory=list)
    bash_low_extra: list[str] = field(default_factory=list)
    # Path patterns for Write/Edit risk elevation
    path_critical: list[str] = field(default_factory=list)
    path_high: list[str] = field(default_factory=list)


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

    # [risk.bash_*]
    for level, attr in [
        ("bash_critical", "bash_critical_extra"),
        ("bash_high", "bash_high_extra"),
        ("bash_low", "bash_low_extra"),
    ]:
        patterns = data.get("risk", {}).get(level, {}).get("patterns", [])
        if isinstance(patterns, list):
            setattr(settings, attr, [str(p) for p in patterns])

    # [risk.path_*]
    for level, attr in [
        ("path_critical", "path_critical"),
        ("path_high", "path_high"),
    ]:
        patterns = data.get("risk", {}).get(level, {}).get("patterns", [])
        if isinstance(patterns, list):
            setattr(settings, attr, [str(p) for p in patterns])

    return settings
