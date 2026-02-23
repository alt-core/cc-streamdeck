"""Render permission requests as 6 Stream Deck Mini button images."""

from __future__ import annotations

from importlib.resources import files

from PIL import Image, ImageDraw, ImageFont

from .config import GRID_COLS, GRID_ROWS
from .protocol import PermissionChoice, PermissionRequest

# Height of the choice label strip at the bottom of choice keys
CHOICE_LABEL_HEIGHT = 20

CHOICE_COLORS = {
    "allow": "#005000",
    "deny": "#800000",
    "always_off": "#000040",
    "always_on": "#0050D0",
    "allow_always": "#0050D0",
}

# Font sizes: M PLUS 1 Code (AA) for 20/16, PixelMplus10 (dot-by-dot) for 10
FONT_SIZE_LARGE = 20
FONT_SIZE_MEDIUM = 16
FONT_SIZE_SMALL = 10

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def load_font(weight: str = "regular", size: int = FONT_SIZE_SMALL) -> ImageFont.FreeTypeFont:
    """Load a bundled font with caching.

    Uses M PLUS 1 Code (antialiased) for sizes > 10,
    PixelMplus10 (pixel-perfect) for size 10.
    """
    key = (weight, size)
    if key not in _font_cache:
        suffix = "Bold" if weight == "bold" else "Regular"
        if size <= FONT_SIZE_SMALL:
            font_name = f"PixelMplus10-{suffix}.ttf"
        else:
            font_name = f"Mplus1Code-{suffix}.ttf"
        font_path = files("cc_streamdeck.fonts").joinpath(font_name)
        _font_cache[key] = ImageFont.truetype(str(font_path), size)
    return _font_cache[key]


def compute_layout(
    num_choices: int, grid_cols: int = GRID_COLS, grid_rows: int = GRID_ROWS
) -> tuple[list[int], list[int]]:
    """Return (message_only_keys, choice_keys) for a given number of choices.

    All keys display message text. Choice keys additionally show
    a label strip at the bottom (CHOICE_LABEL_HEIGHT pixels).

    Choice keys are placed on the bottom row, right-aligned:
    - Allow = bottom-right (always present)
    - Deny = left of Allow
    - Always = between Deny and Allow (if 3 choices)

    Works for any grid size (3x2 Mini, 5x3 Original, 4x2 Plus, etc.).
    """
    total_keys = grid_cols * grid_rows
    all_keys = list(range(total_keys))

    # Bottom-right key is always Allow
    bottom_right = total_keys - 1

    if num_choices >= 3:
        # Allow(right), Deny(right-2), Always(right-1)
        allow_key = bottom_right
        always_key = bottom_right - 1
        deny_key = bottom_right - 2
        choice_keys = [allow_key, deny_key, always_key]
    elif num_choices == 2:
        allow_key = bottom_right
        deny_key = bottom_right - 1
        choice_keys = [allow_key, deny_key]
    else:
        choice_keys = [bottom_right]

    msg_keys = [k for k in all_keys if k not in choice_keys]
    return (msg_keys, choice_keys)


def extract_display_content(tool_name: str, tool_input: dict) -> str:
    """Extract the most relevant content from tool_input for display."""
    field_map = {
        "Bash": "command",
        "Write": "file_path",
        "Edit": "file_path",
        "Read": "file_path",
        "Glob": "pattern",
        "Grep": "pattern",
        "WebFetch": "url",
        "WebSearch": "query",
    }
    field = field_map.get(tool_name)
    if field and field in tool_input:
        return str(tool_input[field])
    for v in tool_input.values():
        if isinstance(v, str) and v:
            return v
    return str(tool_input)[:200] if tool_input else ""


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            test = current + char
            if font.getlength(test) > max_width:
                if current:
                    lines.append(current)
                current = char
            else:
                current = test
        if current:
            lines.append(current)
    return lines


def _key_position(key: int, grid_cols: int = GRID_COLS) -> tuple[int, int]:
    """Return (col, row) for a key index."""
    return (key % grid_cols, key // grid_cols)


def _render_text_on_canvas(
    vw: int,
    vh: int,
    text_max_y: int,
    tool_name: str,
    content: str,
    font_size: int,
    bg_color: str = "black",
    header_bg_color: str = "#101010",
    header_fg_color: str = "#808080",
    body_fg_color: str = "white",
) -> Image.Image:
    """Render header + content text on a virtual canvas."""
    virtual = Image.new("RGB", (vw, vh), bg_color)
    draw = ImageDraw.Draw(virtual)

    header_size = FONT_SIZE_LARGE if font_size == FONT_SIZE_SMALL else font_size
    header_font = load_font("bold", header_size)
    font_regular = load_font("regular", font_size)
    _, header_descent = header_font.getmetrics()
    line_height = font_size

    # Shift header up by descent so text starts at pixel y=0
    y = -header_descent

    # Header background strip
    draw.rectangle([(0, 0), (vw, header_size - 1)], fill=header_bg_color)

    # Tool name header (20px when content is 10px, otherwise same as content)
    draw.text((0, y), f" {tool_name}", font=header_font, fill=header_fg_color)
    y += header_size

    # Content text
    wrapped = _wrap_text(content, font_regular, vw)

    for i, line in enumerate(wrapped):
        if y + line_height > text_max_y:
            break
        # Show "..." on last visible line if more content follows
        next_overflows = y + 2 * line_height > text_max_y
        if next_overflows and i < len(wrapped) - 1:
            line = line.rstrip() + "..."
        draw.text((0, y), line, font=font_regular, fill=body_fg_color)
        y += line_height

    return virtual


def _text_fits(
    vw: int,
    text_max_y: int,
    tool_name: str,
    content: str,
    font_size: int,
) -> bool:
    """Check if all text fits within the available area at the given font size."""
    header_size = FONT_SIZE_LARGE if font_size == FONT_SIZE_SMALL else font_size
    header_font = load_font("bold", header_size)
    font_regular = load_font("regular", font_size)
    _, header_descent = header_font.getmetrics()
    line_height = font_size

    wrapped = _wrap_text(content, font_regular, vw)
    needed_y = -header_descent + header_size + len(wrapped) * line_height
    return needed_y <= text_max_y


def _choose_font_size(
    vw: int,
    text_max_y: int,
    tool_name: str,
    content: str,
) -> int:
    """Select the best font size: 20 -> 16 -> 10, picking the largest that fits."""
    for size in [FONT_SIZE_LARGE, FONT_SIZE_MEDIUM, FONT_SIZE_SMALL]:
        if _text_fits(vw, text_max_y, tool_name, content, size):
            return size
    # Nothing fits — use smallest and truncate with "..."
    return FONT_SIZE_SMALL


def _choice_appearance(choice: PermissionChoice, always_active: bool) -> tuple[str, str, str]:
    """Return (label, bg_color, text_color) for a choice button."""
    if choice.updated_permissions:
        if always_active:
            return (choice.label, CHOICE_COLORS["always_on"], "white")
        return (choice.label, CHOICE_COLORS["always_off"], "#808080")
    if choice.behavior == "deny":
        return (choice.label, CHOICE_COLORS["deny"], "white")
    if always_active:
        return (choice.label, CHOICE_COLORS["allow_always"], "white")
    return (choice.label, CHOICE_COLORS["allow"], "white")


def _overlay_choice_label(
    tile: Image.Image, label: str, bg_color: str, text_color: str = "white"
) -> Image.Image:
    """Overlay a colored choice label strip at the bottom of a tile."""
    tile = tile.copy()
    draw = ImageDraw.Draw(tile)
    tw, th = tile.size

    y_top = th - CHOICE_LABEL_HEIGHT
    draw.rectangle(
        [(0, y_top), (tw, th)],
        fill=bg_color,
    )

    font = load_font("bold", FONT_SIZE_LARGE)
    draw.text(
        (tw // 2, y_top + CHOICE_LABEL_HEIGHT // 2),
        label,
        font=font,
        fill=text_color,
        anchor="mm",
    )
    return tile


def render_permission_request(
    request: PermissionRequest,
    key_image_format: dict,
    always_active: bool = False,
    bg_color: str = "black",
    header_bg_color: str = "#101010",
    header_fg_color: str = "#808080",
    body_fg_color: str = "white",
    grid_cols: int = GRID_COLS,
    grid_rows: int = GRID_ROWS,
) -> dict[int, bytes]:
    """Render button images for a permission request.

    All buttons display message text. Choice buttons additionally
    show a colored label strip at the bottom (CHOICE_LABEL_HEIGHT px).

    Returns {key_index: native_format_bytes}.
    """
    num_choices = len(request.choices)
    _, choice_keys = compute_layout(num_choices, grid_cols, grid_rows)

    # Key pixel size from format (device-dependent)
    key_w, key_h = key_image_format["size"]

    # Virtual canvas spans all keys (gap-free)
    vw = grid_cols * key_w
    vh = grid_rows * key_h

    # Text must not overlap the choice label region
    if choice_keys:
        choice_row = max(k // grid_cols for k in choice_keys)
        text_max_y = choice_row * key_h + (key_h - CHOICE_LABEL_HEIGHT)
    else:
        text_max_y = vh

    tool_name = request.tool_name
    content = extract_display_content(tool_name, request.tool_input)

    # Adaptive font size: 20 → 16 → 10 (truncate at 10 if still overflows)
    font_size = _choose_font_size(vw, text_max_y, tool_name, content)

    virtual = _render_text_on_canvas(
        vw, vh, text_max_y, tool_name, content, font_size,
        bg_color=bg_color,
        header_bg_color=header_bg_color,
        header_fg_color=header_fg_color,
        body_fg_color=body_fg_color,
    )

    # Split into per-key tiles and overlay choice labels
    result: dict[int, bytes] = {}
    for key in range(grid_cols * grid_rows):
        col, row = _key_position(key, grid_cols)
        x = col * key_w
        y = row * key_h
        tile = virtual.crop((x, y, x + key_w, y + key_h))

        if key in choice_keys:
            idx = choice_keys.index(key)
            if idx < num_choices:
                label, bg_color, text_color = _choice_appearance(
                    request.choices[idx], always_active
                )
                tile = _overlay_choice_label(tile, label, bg_color, text_color)

        result[key] = pil_to_native(tile, key_image_format)

    return result


def pil_to_native(image: Image.Image, key_image_format: dict) -> bytes:
    """Convert a PIL image to Stream Deck native format."""
    from StreamDeck.ImageHelpers import PILHelper

    class _FakeKey:
        def key_image_format(self):
            return key_image_format

    return PILHelper.to_native_key_format(_FakeKey(), image)
