"""Tests for renderer module."""

from cc_streamdeck.config import GRID_COLS, GRID_ROWS, KEY_PIXEL_SIZE
from cc_streamdeck.protocol import PermissionChoice
from cc_streamdeck.renderer import (
    CHOICE_LABEL_HEIGHT,
    FONT_SIZE_LARGE,
    FONT_SIZE_MEDIUM,
    FONT_SIZE_SMALL,
    _choice_appearance,
    _choose_font_size,
    _overlay_choice_label,
    _render_text_on_canvas,
    _text_fits,
    compute_layout,
    extract_display_content,
    load_font,
)


class TestComputeLayout:
    def test_three_choices(self):
        msg_keys, choice_keys = compute_layout(3)
        assert msg_keys == [0, 1, 2]
        # Deny(3), Always(4), Allow(5)
        assert choice_keys == [5, 3, 4]

    def test_two_choices(self):
        msg_keys, choice_keys = compute_layout(2)
        assert msg_keys == [0, 1, 2, 3]
        assert choice_keys == [5, 4]

    def test_one_choice(self):
        msg_keys, choice_keys = compute_layout(1)
        assert msg_keys == [0, 1, 2, 3, 4]
        assert choice_keys == [5]

    def test_all_keys_covered(self):
        for n in [1, 2, 3]:
            msg_keys, choice_keys = compute_layout(n)
            assert sorted(msg_keys + choice_keys) == [0, 1, 2, 3, 4, 5]

    # 5x3 (15-key Original/MK.2)
    def test_15key_three_choices(self):
        msg_keys, choice_keys = compute_layout(3, grid_cols=5, grid_rows=3)
        # Allow=14 (bottom-right), Deny=12, Always=13
        assert choice_keys == [14, 12, 13]
        assert len(msg_keys) == 12
        assert sorted(msg_keys + choice_keys) == list(range(15))

    def test_15key_two_choices(self):
        msg_keys, choice_keys = compute_layout(2, grid_cols=5, grid_rows=3)
        assert choice_keys == [14, 13]
        assert len(msg_keys) == 13

    def test_15key_all_keys_covered(self):
        for n in [1, 2, 3]:
            msg_keys, choice_keys = compute_layout(n, grid_cols=5, grid_rows=3)
            assert sorted(msg_keys + choice_keys) == list(range(15))

    # 4x2 (8-key Plus)
    def test_8key_three_choices(self):
        msg_keys, choice_keys = compute_layout(3, grid_cols=4, grid_rows=2)
        # Allow=7 (bottom-right), Deny=5, Always=6
        assert choice_keys == [7, 5, 6]
        assert sorted(msg_keys + choice_keys) == list(range(8))

    # 8x4 (32-key XL)
    def test_32key_three_choices(self):
        msg_keys, choice_keys = compute_layout(3, grid_cols=8, grid_rows=4)
        assert choice_keys == [31, 29, 30]
        assert sorted(msg_keys + choice_keys) == list(range(32))


class TestExtractDisplayContent:
    def test_bash_command(self):
        assert extract_display_content("Bash", {"command": "ls -la"}) == "ls -la"

    def test_write_file_path(self):
        assert extract_display_content("Write", {"file_path": "/tmp/test.txt"}) == "/tmp/test.txt"

    def test_grep_pattern(self):
        assert extract_display_content("Grep", {"pattern": "TODO"}) == "TODO"

    def test_unknown_tool(self):
        result = extract_display_content("Custom", {"foo": "bar"})
        assert result == "bar"

    def test_empty_input(self):
        assert extract_display_content("Bash", {}) == ""


class TestLoadFont:
    def test_pixelmplus_regular(self):
        font = load_font("regular", FONT_SIZE_SMALL)
        assert font is not None

    def test_pixelmplus_bold(self):
        font = load_font("bold", FONT_SIZE_SMALL)
        assert font is not None

    def test_mplus1code_regular(self):
        font = load_font("regular", FONT_SIZE_LARGE)
        assert font is not None

    def test_mplus1code_bold(self):
        font = load_font("bold", FONT_SIZE_LARGE)
        assert font is not None

    def test_mplus1code_medium_size(self):
        font = load_font("regular", FONT_SIZE_MEDIUM)
        assert font is not None

    def test_caching(self):
        f1 = load_font("regular", FONT_SIZE_SMALL)
        f2 = load_font("regular", FONT_SIZE_SMALL)
        assert f1 is f2

    def test_different_families_by_size(self):
        small = load_font("regular", FONT_SIZE_SMALL)
        large = load_font("regular", FONT_SIZE_LARGE)
        assert small is not large


class TestTextFits:
    def test_short_text_fits_large(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        text_max_y = GRID_ROWS * KEY_PIXEL_SIZE[1]
        assert _text_fits(vw, text_max_y, "Bash", "ls", FONT_SIZE_LARGE)

    def test_long_text_needs_small(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        text_max_y = GRID_ROWS * KEY_PIXEL_SIZE[1] - CHOICE_LABEL_HEIGHT
        long_text = "a" * 500
        assert not _text_fits(vw, text_max_y, "Bash", long_text, FONT_SIZE_LARGE)

    def test_medium_text_fits_medium(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        text_max_y = GRID_ROWS * KEY_PIXEL_SIZE[1] - CHOICE_LABEL_HEIGHT
        assert _text_fits(vw, text_max_y, "Bash", "ls -la", FONT_SIZE_MEDIUM)


class TestChooseFontSize:
    def test_short_text_gets_large(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        text_max_y = GRID_ROWS * KEY_PIXEL_SIZE[1]
        assert _choose_font_size(vw, text_max_y, "Bash", "ls") == FONT_SIZE_LARGE

    def test_medium_text_gets_medium(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        text_max_y = GRID_ROWS * KEY_PIXEL_SIZE[1] - CHOICE_LABEL_HEIGHT
        # Text that fits at 16px but not at 20px
        text = "a" * 80
        if not _text_fits(vw, text_max_y, "Bash", text, FONT_SIZE_LARGE):
            result = _choose_font_size(vw, text_max_y, "Bash", text)
            assert result in (FONT_SIZE_MEDIUM, FONT_SIZE_SMALL)

    def test_very_long_text_gets_small(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        text_max_y = GRID_ROWS * KEY_PIXEL_SIZE[1] - CHOICE_LABEL_HEIGHT
        long_text = "a" * 500
        assert _choose_font_size(vw, text_max_y, "Bash", long_text) == FONT_SIZE_SMALL


class TestRenderTextOnCanvas:
    def test_canvas_size(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        vh = GRID_ROWS * KEY_PIXEL_SIZE[1]
        img = _render_text_on_canvas(vw, vh, vh, "Bash", "ls -la", FONT_SIZE_LARGE)
        assert img.size == (vw, vh)

    def test_has_content(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        vh = GRID_ROWS * KEY_PIXEL_SIZE[1]
        img = _render_text_on_canvas(vw, vh, vh, "Bash", "ls -la", FONT_SIZE_LARGE)
        extrema = img.getextrema()
        assert any(ch[1] > 0 for ch in extrema)

    def test_custom_bg_color(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        vh = GRID_ROWS * KEY_PIXEL_SIZE[1]
        # Render with a non-black body background
        img = _render_text_on_canvas(
            vw, vh, vh, "Bash", "ls", FONT_SIZE_LARGE,
            bg_color="#0A0A20",
        )
        # Check that the image has blue channel values from the bg
        # The bottom area (below header and text) should have bg color
        pixel = img.getpixel((vw // 2, vh - 1))
        assert pixel[2] >= 0x20  # blue channel from #0A0A20

    def test_header_bg_color(self):
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        vh = GRID_ROWS * KEY_PIXEL_SIZE[1]
        img = _render_text_on_canvas(
            vw, vh, vh, "Bash", "ls", FONT_SIZE_LARGE,
            header_bg_color="#800000",
        )
        # Top-left area should have red from header background
        # Check a pixel in the header region that's not covered by text
        pixel = img.getpixel((vw - 1, 0))
        assert pixel[0] >= 0x80  # red channel from #800000


class TestChoiceAppearance:
    def test_allow_normal(self):
        choice = PermissionChoice(label="Allow", behavior="allow")
        label, color, text_color = _choice_appearance(choice, always_active=False)
        assert label == "Allow"
        assert color == "#005000"
        assert text_color == "white"

    def test_allow_with_always_active(self):
        choice = PermissionChoice(label="Allow", behavior="allow")
        label, color, text_color = _choice_appearance(choice, always_active=True)
        assert label == "Allow"
        assert color == "#0050D0"  # same as always_on
        assert text_color == "white"

    def test_deny(self):
        choice = PermissionChoice(label="Deny", behavior="deny")
        label, color, text_color = _choice_appearance(choice, always_active=False)
        assert label == "Deny"
        assert color == "#800000"
        assert text_color == "white"

    def test_always_inactive(self):
        choice = PermissionChoice(
            label="Always", behavior="allow",
            updated_permissions=[{"type": "toolAlwaysAllow"}],
        )
        label, color, text_color = _choice_appearance(choice, always_active=False)
        assert label == "Always"
        assert color == "#000040"  # always_off
        assert text_color == "#808080"  # gray when inactive

    def test_always_active(self):
        choice = PermissionChoice(
            label="Always", behavior="allow",
            updated_permissions=[{"type": "toolAlwaysAllow"}],
        )
        label, color, text_color = _choice_appearance(choice, always_active=True)
        assert label == "Always"
        assert color == "#0050D0"  # always_on
        assert text_color == "white"


class TestOverlayChoiceLabel:
    def test_returns_correct_size(self):
        from PIL import Image

        tile = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        result = _overlay_choice_label(tile, "Allow", "#005000")
        assert result.size == KEY_PIXEL_SIZE

    def test_does_not_mutate_original(self):
        from PIL import Image

        tile = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        original_data = tile.tobytes()
        _overlay_choice_label(tile, "Allow", "#005000")
        assert tile.tobytes() == original_data

    def test_bottom_strip_has_color(self):
        from PIL import Image

        tile = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        result = _overlay_choice_label(tile, "Allow", "#005000")
        # Check the bottom strip has non-black pixels
        bottom = result.crop((0, KEY_PIXEL_SIZE[1] - CHOICE_LABEL_HEIGHT, KEY_PIXEL_SIZE[0], KEY_PIXEL_SIZE[1]))
        extrema = bottom.getextrema()
        assert any(ch[1] > 0 for ch in extrema)


class TestRenderPermissionRequest:
    """Integration tests using mock key_image_format."""

    MOCK_FORMAT = {
        "size": (80, 80),
        "format": "BMP",
        "flip": (False, True),
        "rotation": 90,
    }

    def test_returns_all_6_keys(self, sample_request):
        from cc_streamdeck.renderer import render_permission_request

        result = render_permission_request(sample_request, self.MOCK_FORMAT)
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_returns_bytes(self, sample_request):
        from cc_streamdeck.renderer import render_permission_request

        result = render_permission_request(sample_request, self.MOCK_FORMAT)
        for v in result.values():
            assert isinstance(v, bytes)
            assert len(v) > 0

    def test_two_choice_layout(self, two_choice_request):
        from cc_streamdeck.renderer import render_permission_request

        result = render_permission_request(two_choice_request, self.MOCK_FORMAT)
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_always_active_renders(self, sample_request):
        from cc_streamdeck.renderer import render_permission_request

        result = render_permission_request(
            sample_request, self.MOCK_FORMAT, always_active=True
        )
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}
        for v in result.values():
            assert isinstance(v, bytes)


class TestTruncation:
    def test_overflow_text_still_renders(self):
        """Even very long text should render without error."""
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        vh = GRID_ROWS * KEY_PIXEL_SIZE[1]
        text_max_y = vh - CHOICE_LABEL_HEIGHT
        long_text = "x" * 2000
        img = _render_text_on_canvas(vw, vh, text_max_y, "Bash", long_text, FONT_SIZE_SMALL)
        assert img.size == (vw, vh)
        # Should have rendered something
        extrema = img.getextrema()
        assert any(ch[1] > 0 for ch in extrema)
