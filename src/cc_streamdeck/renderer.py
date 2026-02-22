"""Render permission requests as 6 Stream Deck Mini button images."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from .config import GRID_COLS, KEY_PIXEL_SIZE
from .protocol import PermissionChoice, PermissionRequest

if TYPE_CHECKING:
    pass

# Inter-key gap on Stream Deck Mini (approximate physical spacing in pixels)
KEY_SPACING = (18, 18)

CHOICE_COLORS = {
    "allow": "#005000",
    "deny": "#800000",
    "always": "#000080",
}

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def load_font(weight: str = "regular", size: int = 10) -> ImageFont.FreeTypeFont:
    """Load a bundled PixelMplus10 font with caching."""
    key = (weight, size)
    if key not in _font_cache:
        suffix = "Bold" if weight == "bold" else "Regular"
        font_name = f"PixelMplus10-{suffix}.ttf"
        font_path = files("cc_streamdeck.fonts").joinpath(font_name)
        _font_cache[key] = ImageFont.truetype(str(font_path), size)
    return _font_cache[key]


def compute_layout(num_choices: int) -> tuple[list[int], list[int]]:
    """Return (message_keys, choice_keys) for a given number of choices.

    Stream Deck Mini layout (key indices):
        [0] [1] [2]
        [3] [4] [5]
    """
    if num_choices >= 3:
        return ([0, 1, 2], [3, 4, 5])
    elif num_choices == 2:
        return ([0, 1, 2, 3], [4, 5])
    else:
        return ([0, 1, 2, 3, 4], [5])


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
    # Fallback: first non-empty string value
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
            bbox = font.getbbox(test)
            if bbox[2] - bbox[0] > max_width:
                if current:
                    lines.append(current)
                current = char
            else:
                current = test
        if current:
            lines.append(current)
    return lines


def _key_position(key: int) -> tuple[int, int]:
    """Return (col, row) for a key index."""
    return (key % GRID_COLS, key // GRID_COLS)


def create_message_tiles(
    tool_name: str,
    tool_input: dict,
    message_keys: list[int],
) -> dict[int, Image.Image]:
    """Create per-key tile images for the message area.

    Renders text onto a gap-free virtual canvas (visible area only),
    then splits into per-key 80x80 tiles. This ensures no text falls
    into the physical gap between buttons.
    """
    positions = [_key_position(k) for k in message_keys]
    min_col = min(c for c, _ in positions)
    max_col = max(c for c, _ in positions)
    min_row = min(r for _, r in positions)
    max_row = max(r for _, r in positions)

    num_cols = max_col - min_col + 1
    num_rows = max_row - min_row + 1

    # Gap-free virtual canvas: only visible pixels
    vw = num_cols * KEY_PIXEL_SIZE[0]
    vh = num_rows * KEY_PIXEL_SIZE[1]

    virtual = Image.new("RGB", (vw, vh), "black")
    draw = ImageDraw.Draw(virtual)

    font_bold = load_font("bold", 20)
    font_regular = load_font("regular", 20)

    # Tool name header
    draw.text((2, 0), tool_name, font=font_bold, fill="#00BFFF")
    header_height = font_bold.getbbox(tool_name)[3] + 4

    # Content text
    content = extract_display_content(tool_name, tool_input)
    wrapped = _wrap_text(content, font_regular, vw - 4)

    y = header_height
    line_height = 20
    for line in wrapped:
        if y + line_height > vh:
            break
        draw.text((2, y), line, font=font_regular, fill="white")
        y += line_height

    # Split virtual canvas into per-key tiles
    tiles: dict[int, Image.Image] = {}
    for key in message_keys:
        col, row = _key_position(key)
        rel_col = col - min_col
        rel_row = row - min_row
        x = rel_col * KEY_PIXEL_SIZE[0]
        y = rel_row * KEY_PIXEL_SIZE[1]
        tiles[key] = virtual.crop((x, y, x + KEY_PIXEL_SIZE[0], y + KEY_PIXEL_SIZE[1]))

    return tiles


def create_choice_image(choice: PermissionChoice) -> Image.Image:
    """Create an 80x80 image for a choice button."""
    if choice.behavior == "deny":
        bg = CHOICE_COLORS["deny"]
    elif choice.updated_permissions:
        bg = CHOICE_COLORS["always"]
    else:
        bg = CHOICE_COLORS["allow"]

    img = Image.new("RGB", KEY_PIXEL_SIZE, bg)
    draw = ImageDraw.Draw(img)
    font = load_font("bold", 20)

    draw.text(
        (KEY_PIXEL_SIZE[0] // 2, KEY_PIXEL_SIZE[1] // 2),
        choice.label,
        font=font,
        fill="white",
        anchor="mm",
    )
    return img


def pil_to_native(image: Image.Image, key_image_format: dict) -> bytes:
    """Convert a PIL image to Stream Deck native format."""
    from StreamDeck.ImageHelpers import PILHelper

    # PILHelper.to_native_key_format needs a deck-like object
    # We create a minimal wrapper
    class _FakeKey:
        def key_image_format(self):
            return key_image_format

    return PILHelper.to_native_key_format(_FakeKey(), image)


def render_permission_request(
    request: PermissionRequest,
    key_image_format: dict,
) -> dict[int, bytes]:
    """Render all 6 button images for a permission request.

    Returns {key_index: native_format_bytes}.
    """
    num_choices = len(request.choices)
    message_keys, choice_keys = compute_layout(num_choices)

    # Message area
    tiles = create_message_tiles(request.tool_name, request.tool_input, message_keys)

    result: dict[int, bytes] = {}

    for key, tile in tiles.items():
        result[key] = pil_to_native(tile, key_image_format)

    # Choice buttons
    for i, key in enumerate(choice_keys):
        if i < num_choices:
            choice_img = create_choice_image(request.choices[i])
        else:
            choice_img = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        result[key] = pil_to_native(choice_img, key_image_format)

    return result
