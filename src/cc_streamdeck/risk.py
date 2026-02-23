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

# --- Built-in Bash rules: (name, regex_pattern, level) ---
# Order: critical -> low -> high (preserves current match priority)

BashRule = tuple[str, str, RiskLevel]

BUILTIN_BASH_RULES: list[BashRule] = [
    # --- critical ---
    ("rm-recursive", r"\brm\s+.*-[^\s]*r", "critical"),
    ("rm-rf", r"\brm\s+-rf\b", "critical"),
    ("sudo", r"\bsudo\b", "critical"),
    ("chmod-777", r"\bchmod\s+777\b", "critical"),
    ("chmod-recursive", r"\bchmod\s+-R\b", "critical"),
    ("mkfs", r"\bmkfs\b", "critical"),
    ("dd", r"\bdd\s+", "critical"),
    ("git-push-force", r"\bgit\s+push\s+.*--force", "critical"),
    ("git-push-f", r"\bgit\s+push\s+-f\b", "critical"),
    ("git-reset-hard", r"\bgit\s+reset\s+--hard", "critical"),
    ("git-clean-f", r"\bgit\s+clean\s+-[^\s]*f", "critical"),
    ("docker-rm", r"\bdocker\s+(rm|rmi)\b", "critical"),
    ("docker-prune", r"\bdocker\s+system\s+prune", "critical"),
    ("kubectl-delete", r"\bkubectl\s+delete\b", "critical"),
    ("drop-table", r"DROP\s+(TABLE|DATABASE)", "critical"),
    ("system-control", r"\b(shutdown|reboot|halt|poweroff)\b", "critical"),
    ("curl-pipe-bash", r"\bcurl\b.*\|\s*(sudo\s+)?bash", "critical"),
    ("wget-pipe-bash", r"\bwget\b.*\|\s*(sudo\s+)?bash", "critical"),
    ("raw-device-write", r">\s*/dev/sd[a-z]", "critical"),
    # --- low ---
    ("read-utils", r"^\s*(ls|cat|head|tail|wc|echo|pwd|whoami|date|which|type|file|stat)\b", "low"),
    ("search-tools", r"^\s*(grep|rg|find|fd|tree|du|df)\b", "low"),
    ("git-readonly", r"^\s*(git\s+(status|log|diff|show))\b", "low"),
    ("test-npm", r"^\s*(npm\s+test|npm\s+run\s+test)\b", "low"),
    ("test-npx", r"^\s*(npx\s+jest|npx\s+vitest)\b", "low"),
    ("test-uv", r"^\s*(uv\s+run\s+(pytest|ruff))\b", "low"),
    ("test-cargo", r"^\s*(cargo\s+(test|check))\b", "low"),
    ("test-python", r"^\s*(python|python3)\s+-m\s+pytest\b", "low"),
    # --- high ---
    ("rm", r"\brm\b", "high"),
    ("git-push", r"\bgit\s+push\b", "high"),
    ("git-checkout-dot", r"\bgit\s+checkout\s+\.", "high"),
    ("git-restore", r"\bgit\s+restore\b", "high"),
    ("git-stash-drop", r"\bgit\s+stash\s+drop", "high"),
    ("npm-publish", r"\bnpm\s+publish\b", "high"),
    ("pip-install", r"\bpip\s+install\b", "high"),
    ("curl", r"\bcurl\b", "high"),
    ("wget", r"\bwget\b", "high"),
    ("mv", r"\bmv\b", "high"),
    ("chmod", r"\bchmod\b", "high"),
    ("chown", r"\bchown\b", "high"),
]

# Built-in path patterns for Write/Edit risk elevation
BUILTIN_PATH_CRITICAL: list[str] = []
BUILTIN_PATH_HIGH: list[str] = []


def _parse_pattern(raw: str) -> re.Pattern:
    """Parse a pattern string into a compiled regex.

    Supports two modes:
    1. ``regex:...`` prefix → raw regex
    2. Otherwise → simple pattern syntax:
       - Words are escaped and joined with ``\\s+``
       - ``*`` becomes ``.*``
       - Word boundaries ``\\b`` added at start/end (unless ``.*``)
    """
    if raw.startswith("regex:"):
        return re.compile(raw[6:], re.IGNORECASE)

    # Simple pattern conversion
    parts = raw.split()
    regex_parts = []
    for part in parts:
        if "*" in part:
            segments = part.split("*")
            regex_parts.append(".*".join(re.escape(s) for s in segments))
        else:
            regex_parts.append(re.escape(part))
    inner = r"\s+".join(regex_parts)
    prefix = "" if inner.startswith(".*") else r"\b"
    suffix = "" if inner.endswith(".*") else r"\b"
    return re.compile(prefix + inner + suffix, re.IGNORECASE)


@dataclass
class CompiledBashRule:
    """A compiled Bash pattern rule with name and risk level."""

    name: str
    pattern: re.Pattern
    level: RiskLevel


@dataclass
class RiskConfig:
    """Loaded risk configuration (defaults + user overrides)."""

    risk_colors: dict[RiskLevel, tuple[str, str]] = field(
        default_factory=lambda: dict(DEFAULT_RISK_COLORS)
    )
    instance_palette: list[str] = field(default_factory=lambda: list(DEFAULT_INSTANCE_PALETTE))
    body_text_color: str = DEFAULT_BODY_TEXT_COLOR
    tool_risk: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOL_RISK))
    tool_risk_fallback: RiskLevel = DEFAULT_TOOL_RISK_FALLBACK
    bash_rules: list[CompiledBashRule] = field(default_factory=list)
    path_critical: list[re.Pattern] = field(default_factory=list)
    path_high: list[re.Pattern] = field(default_factory=list)


def _compile_path_patterns(builtin: list[str], extra: list[str]) -> list[re.Pattern]:
    """Compile built-in + user extra path patterns into regex objects."""
    patterns = []
    for p in builtin + extra:
        try:
            patterns.append(re.compile(p, re.IGNORECASE))
        except re.error:
            pass  # skip malformed user patterns
    return patterns


def _build_bash_rules(settings: UserSettings) -> list[CompiledBashRule]:
    """Build the ordered list of compiled Bash rules.

    Order: prepend (user) -> built-in (with level overrides) -> append (user)
    """
    rules: list[CompiledBashRule] = []

    # 1. Prepend rules (user-defined, checked first)
    _compile_user_rules(settings.bash_prepend, rules)

    # 2. Built-in rules (with level overrides from bash_levels)
    for name, regex_str, default_level in BUILTIN_BASH_RULES:
        level = settings.bash_levels.get(name, default_level)
        if level not in RISK_ORDER:
            level = default_level
        try:
            compiled = re.compile(regex_str, re.IGNORECASE)
            rules.append(CompiledBashRule(name=name, pattern=compiled, level=level))  # type: ignore[arg-type]
        except re.error:
            pass  # should never happen for built-in patterns

    # 3. Append rules (user-defined, checked after built-in)
    _compile_user_rules(settings.bash_append, rules)

    # Apply bash_levels overrides to user-defined rules (prepend/append)
    for rule in rules:
        if rule.name in settings.bash_levels:
            new_level = settings.bash_levels[rule.name]
            if new_level in RISK_ORDER:
                rule.level = new_level  # type: ignore[assignment]

    return rules


def _compile_user_rules(entries: list[dict[str, str]], rules: list[CompiledBashRule]) -> None:
    """Compile user-defined rules and append to rules list."""
    for entry in entries:
        name = entry.get("name", "")
        pattern_str = entry.get("pattern", "")
        level = entry.get("level", "medium")
        if not name or not pattern_str:
            continue
        if level not in RISK_ORDER:
            continue
        try:
            compiled = _parse_pattern(pattern_str)
            rules.append(CompiledBashRule(name=name, pattern=compiled, level=level))  # type: ignore[arg-type]
        except re.error:
            pass


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

    # Build bash rules
    config.bash_rules = _build_bash_rules(settings)

    # Compile path patterns
    config.path_critical = _compile_path_patterns(BUILTIN_PATH_CRITICAL, settings.path_critical)
    config.path_high = _compile_path_patterns(BUILTIN_PATH_HIGH, settings.path_high)

    return config


def _max_risk(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """Return the higher of two risk levels."""
    return a if RISK_ORDER[a] >= RISK_ORDER[b] else b


def _assess_bash(command: str, config: RiskConfig) -> RiskLevel:
    """Pattern-match a Bash command against the ordered rule list."""
    for rule in config.bash_rules:
        if rule.pattern.search(command):
            return rule.level
    return config.tool_risk_fallback


def assess_risk_verbose(
    tool_name: str, tool_input: dict, config: RiskConfig
) -> tuple[RiskLevel, str]:
    """Like assess_risk but also returns the matched rule name (or empty)."""
    tool_setting = config.tool_risk.get(tool_name)

    if tool_setting is None and tool_name.startswith("mcp__"):
        tool_setting = config.tool_risk.get("mcp", None)

    if tool_setting is None:
        tool_setting = config.tool_risk_fallback

    if tool_setting == "evaluate":
        command = tool_input.get("command", "")
        for rule in config.bash_rules:
            if rule.pattern.search(command):
                return rule.level, rule.name
        return config.tool_risk_fallback, ""

    base_level: RiskLevel = (
        tool_setting if tool_setting in RISK_ORDER else config.tool_risk_fallback
    )  # type: ignore[assignment]
    matched = ""

    if tool_name in ("Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        if file_path:
            elevation = _check_path_elevation(file_path, config)
            if elevation is not None:
                base_level = _max_risk(base_level, elevation)
                matched = "path-elevation"

    return base_level, matched


def _check_path_elevation(file_path: str, config: RiskConfig) -> RiskLevel | None:
    """Check file path against path patterns. Returns elevated level or None."""
    for pat in config.path_critical:
        if pat.search(file_path):
            return "critical"
    for pat in config.path_high:
        if pat.search(file_path):
            return "high"
    return None


def assess_risk(tool_name: str, tool_input: dict, config: RiskConfig) -> RiskLevel:
    """Determine risk level for a permission request."""
    level, _ = assess_risk_verbose(tool_name, tool_input, config)
    return level


def instance_palette_index(client_pid: int, seen_pids: list[int]) -> int:
    """Return palette index for a client_pid based on first-seen order."""
    if client_pid not in seen_pids:
        seen_pids.append(client_pid)
    return seen_pids.index(client_pid)
