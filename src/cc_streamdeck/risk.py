"""Risk assessment for permission requests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .settings import UserSettings

RiskLevel = Literal["critical", "high", "medium", "low"]

RISK_ORDER: dict[RiskLevel, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Default header colors per risk level: (background, foreground)
DEFAULT_RISK_COLORS: dict[RiskLevel, tuple[str, str]] = {
    "critical": ("#800000", "#FFFFFF"),
    "high": ("#604000", "#FFD080"),
    "medium": ("#203050", "#80C0FF"),
    "low": ("#101010", "#808080"),
}

# Default instance identification palette (body background colors)
DEFAULT_INSTANCE_PALETTE: list[str] = [
    "#0A0A20",  # dark navy
    "#0A200A",  # dark green
    "#200A0A",  # dark maroon
    "#1A1A0A",  # dark khaki
    "#150A20",  # dark purple
]

# Default body text color
DEFAULT_BODY_TEXT_COLOR = "white"

# Default tool risk levels
DEFAULT_TOOL_RISK: dict[str, str] = {
    "Bash": "evaluate",
    "Write": "high",
    "Edit": "medium",
    "WebFetch": "medium",
    "WebSearch": "low",
    "Task": "low",
}
DEFAULT_TOOL_RISK_FALLBACK: RiskLevel = "medium"

# --- Built-in Bash patterns ---

BUILTIN_CRITICAL_PATTERNS: list[str] = [
    r"\brm\s+.*-[^\s]*r",             # rm -rf, rm -r, rm -fr, etc.
    r"\brm\s+-rf\b",                   # explicit rm -rf
    r"\bsudo\b",                       # any sudo
    r"\bchmod\s+777\b",               # chmod 777
    r"\bchmod\s+-R\b",                # recursive chmod
    r"\bmkfs\b",                       # format filesystem
    r"\bdd\s+",                        # disk dump
    r"\bgit\s+push\s+.*--force",       # git push --force
    r"\bgit\s+push\s+-f\b",           # git push -f
    r"\bgit\s+reset\s+--hard",        # git reset --hard
    r"\bgit\s+clean\s+-[^\s]*f",      # git clean -f
    r"\bdocker\s+(rm|rmi)\b",         # docker destructive
    r"\bdocker\s+system\s+prune",     # docker system prune
    r"\bkubectl\s+delete\b",          # k8s delete
    r"DROP\s+(TABLE|DATABASE)",        # SQL destructive
    r"\b(shutdown|reboot|halt|poweroff)\b",  # system control
    r"\bcurl\b.*\|\s*(sudo\s+)?bash",  # curl pipe to bash
    r"\bwget\b.*\|\s*(sudo\s+)?bash",  # wget pipe to bash
    r">\s*/dev/sd[a-z]",              # write to raw device
]

BUILTIN_HIGH_PATTERNS: list[str] = [
    r"\brm\b",                         # any rm (without -rf)
    r"\bgit\s+push\b",                # git push (non-force)
    r"\bgit\s+checkout\s+\.",         # git checkout . (discard changes)
    r"\bgit\s+restore\b",            # git restore
    r"\bgit\s+stash\s+drop",         # git stash drop
    r"\bnpm\s+publish\b",            # npm publish
    r"\bpip\s+install\b",            # pip install
    r"\bcurl\b",                       # curl (data exfiltration)
    r"\bwget\b",                       # wget
    r"\bmv\b",                         # move/rename files
    r"\bchmod\b",                      # chmod (non-777)
    r"\bchown\b",                      # chown
]

BUILTIN_LOW_PATTERNS: list[str] = [
    r"^\s*(ls|cat|head|tail|wc|echo|pwd|whoami|date|which|type|file|stat)\b",
    r"^\s*(grep|rg|find|fd|tree|du|df)\b",
    r"^\s*(git\s+(status|log|diff|show))\b",
    r"^\s*(npm\s+test|npm\s+run\s+test)\b",
    r"^\s*(npx\s+jest|npx\s+vitest)\b",
    r"^\s*(uv\s+run\s+(pytest|ruff))\b",
    r"^\s*(cargo\s+(test|check))\b",
    r"^\s*(python|python3)\s+-m\s+pytest\b",
]

# Built-in path patterns for Write/Edit risk elevation
BUILTIN_PATH_CRITICAL: list[str] = []
BUILTIN_PATH_HIGH: list[str] = []


@dataclass
class RiskConfig:
    """Loaded risk configuration (defaults + user overrides)."""

    risk_colors: dict[RiskLevel, tuple[str, str]] = field(
        default_factory=lambda: dict(DEFAULT_RISK_COLORS)
    )
    instance_palette: list[str] = field(
        default_factory=lambda: list(DEFAULT_INSTANCE_PALETTE)
    )
    body_text_color: str = DEFAULT_BODY_TEXT_COLOR
    tool_risk: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_TOOL_RISK)
    )
    tool_risk_fallback: RiskLevel = DEFAULT_TOOL_RISK_FALLBACK
    bash_critical: list[re.Pattern] = field(default_factory=list)
    bash_high: list[re.Pattern] = field(default_factory=list)
    bash_low: list[re.Pattern] = field(default_factory=list)
    path_critical: list[re.Pattern] = field(default_factory=list)
    path_high: list[re.Pattern] = field(default_factory=list)


def _compile_patterns(builtin: list[str], extra: list[str]) -> list[re.Pattern]:
    """Compile built-in + user extra patterns into regex objects."""
    patterns = []
    for p in builtin + extra:
        try:
            patterns.append(re.compile(p, re.IGNORECASE))
        except re.error:
            pass  # skip malformed user patterns
    return patterns


def load_risk_config(settings: UserSettings | None = None) -> RiskConfig:
    """Build RiskConfig from defaults + optional user settings."""
    if settings is None:
        settings = UserSettings()

    config = RiskConfig()

    # Merge user risk colors
    for level in ("critical", "high", "medium", "low"):
        level_key: RiskLevel = level  # type: ignore[assignment]
        if level in settings.risk_colors:
            bg, fg = config.risk_colors[level_key]
            user = settings.risk_colors[level]
            bg = user.get("bg", bg)
            fg = user.get("fg", fg)
            config.risk_colors[level_key] = (bg, fg)

    # Merge instance palette
    if settings.instance_palette:
        config.instance_palette = settings.instance_palette

    # Merge body text color
    if settings.body_text_color:
        config.body_text_color = settings.body_text_color

    # Merge tool risk
    for k, v in settings.tool_risk.items():
        config.tool_risk[k] = v
    if settings.tool_risk_default:
        config.tool_risk_fallback = settings.tool_risk_default  # type: ignore[assignment]

    # Compile patterns
    config.bash_critical = _compile_patterns(
        BUILTIN_CRITICAL_PATTERNS, settings.bash_critical_extra
    )
    config.bash_high = _compile_patterns(
        BUILTIN_HIGH_PATTERNS, settings.bash_high_extra
    )
    config.bash_low = _compile_patterns(
        BUILTIN_LOW_PATTERNS, settings.bash_low_extra
    )
    config.path_critical = _compile_patterns(
        BUILTIN_PATH_CRITICAL, settings.path_critical
    )
    config.path_high = _compile_patterns(
        BUILTIN_PATH_HIGH, settings.path_high
    )

    return config


def _max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """Return the higher of two risk levels."""
    return a if RISK_ORDER[a] >= RISK_ORDER[b] else b


def _assess_bash(command: str, config: RiskConfig) -> RiskLevel:
    """Pattern-match a Bash command string."""
    # Critical first
    for pat in config.bash_critical:
        if pat.search(command):
            return "critical"

    # Low (explicitly safe)
    for pat in config.bash_low:
        if pat.search(command):
            return "low"

    # High
    for pat in config.bash_high:
        if pat.search(command):
            return "high"

    # Default for unmatched Bash commands
    return "medium"


def _check_path_elevation(
    file_path: str, config: RiskConfig
) -> RiskLevel | None:
    """Check file path against path patterns. Returns elevated level or None."""
    for pat in config.path_critical:
        if pat.search(file_path):
            return "critical"
    for pat in config.path_high:
        if pat.search(file_path):
            return "high"
    return None


def assess_risk(
    tool_name: str, tool_input: dict, config: RiskConfig
) -> RiskLevel:
    """Determine risk level for a permission request."""
    tool_setting = config.tool_risk.get(tool_name)

    # MCP tools: mcp__server__tool
    if tool_setting is None and tool_name.startswith("mcp__"):
        tool_setting = config.tool_risk.get("mcp", None)

    if tool_setting is None:
        tool_setting = config.tool_risk_fallback

    if tool_setting == "evaluate":
        # Bash: evaluate command content
        command = tool_input.get("command", "")
        return _assess_bash(command, config)

    # Cast to RiskLevel
    base_level: RiskLevel = tool_setting if tool_setting in RISK_ORDER else config.tool_risk_fallback  # type: ignore[assignment]

    # Path-based elevation for Write/Edit
    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            elevation = _check_path_elevation(file_path, config)
            if elevation is not None:
                base_level = _max_risk(base_level, elevation)

    return base_level


def instance_palette_index(client_pid: int, seen_pids: list[int]) -> int:
    """Return palette index for a client_pid based on first-seen order."""
    if client_pid not in seen_pids:
        seen_pids.append(client_pid)
    return seen_pids.index(client_pid)
