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
    _overlay_top_label,
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
            vw,
            vh,
            vh,
            "Bash",
            "ls",
            FONT_SIZE_LARGE,
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
            vw,
            vh,
            vh,
            "Bash",
            "ls",
            FONT_SIZE_LARGE,
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
            label="Always",
            behavior="allow",
            updated_permissions=[{"type": "toolAlwaysAllow"}],
        )
        label, color, text_color = _choice_appearance(choice, always_active=False)
        assert label == "Always"
        assert color == "#000040"  # always_off
        assert text_color == "#808080"  # gray when inactive

    def test_always_active(self):
        choice = PermissionChoice(
            label="Always",
            behavior="allow",
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
        bottom = result.crop(
            (0, KEY_PIXEL_SIZE[1] - CHOICE_LABEL_HEIGHT, KEY_PIXEL_SIZE[0], KEY_PIXEL_SIZE[1])
        )
        extrema = bottom.getextrema()
        assert any(ch[1] > 0 for ch in extrema)


class TestOverlayTopLabel:
    def test_returns_correct_size(self):
        from PIL import Image

        tile = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        result = _overlay_top_label(tile, "Go CC", "#303030")
        assert result.size == KEY_PIXEL_SIZE

    def test_does_not_mutate_original(self):
        from PIL import Image

        tile = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        original_data = tile.tobytes()
        _overlay_top_label(tile, "Go CC", "#303030")
        assert tile.tobytes() == original_data

    def test_top_strip_has_color(self):
        from PIL import Image

        tile = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        result = _overlay_top_label(tile, "Go CC", "#303030")
        # Top strip should have non-black pixels
        top = result.crop((0, 0, KEY_PIXEL_SIZE[0], CHOICE_LABEL_HEIGHT))
        extrema = top.getextrema()
        assert any(ch[1] > 0 for ch in extrema)

    def test_bottom_area_mostly_unchanged(self):
        from PIL import Image

        tile = Image.new("RGB", KEY_PIXEL_SIZE, "black")
        result = _overlay_top_label(tile, "Go CC", "#303030")
        # Area well below the strip (leaving margin for font anti-aliasing)
        margin = CHOICE_LABEL_HEIGHT + 5
        bottom = result.crop(
            (0, margin, KEY_PIXEL_SIZE[0], KEY_PIXEL_SIZE[1])
        )
        extrema = bottom.getextrema()
        assert all(ch == (0, 0) for ch in extrema)


class TestHeaderWidth:
    def test_header_narrower_with_open_key(self):
        """Header background is narrower when header_width is set."""
        vw = GRID_COLS * KEY_PIXEL_SIZE[0]
        vh = GRID_ROWS * KEY_PIXEL_SIZE[1]
        header_w = (GRID_COLS - 1) * KEY_PIXEL_SIZE[0]
        img = _render_text_on_canvas(
            vw, vh, vh, "Bash", "ls", FONT_SIZE_LARGE,
            header_bg_color="#800000", header_width=header_w,
        )
        # Pixel at the right end of the narrowed header should NOT have header color
        # (it's beyond header_width, so it should be body bg = black)
        pixel_outside = img.getpixel((vw - 1, 0))
        assert pixel_outside[0] == 0  # black, not red
        # Pixel inside header width should have header color
        pixel_inside = img.getpixel((header_w - 1, 0))
        assert pixel_inside[0] >= 0x80  # red from #800000


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

        result = render_permission_request(sample_request, self.MOCK_FORMAT, always_active=True)
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}
        for v in result.values():
            assert isinstance(v, bytes)

    def test_open_key_overlay(self, sample_request):
        """open_key adds a 'Go CC' top label overlay on the top-right key."""
        from cc_streamdeck.renderer import render_permission_request

        # Top-right key on 3x2 grid = key 2
        result_with = render_permission_request(
            sample_request, self.MOCK_FORMAT, open_key=2,
        )
        result_without = render_permission_request(
            sample_request, self.MOCK_FORMAT,
        )
        # Key 2 (top-right) should differ: Go CC overlay vs plain header
        assert result_with[2] != result_without[2]
        # Choice keys should be unchanged (Deny still present)
        assert result_with[3] == result_without[3]
        assert result_with[5] == result_without[5]


class TestRenderFallbackMessage:
    MOCK_FORMAT = {
        "size": (80, 80),
        "format": "BMP",
        "flip": (False, True),
        "rotation": 90,
    }

    def test_returns_all_keys(self):
        from cc_streamdeck.renderer import render_fallback_message

        result = render_fallback_message("ExitPlanMode", self.MOCK_FORMAT)
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_returns_bytes(self):
        from cc_streamdeck.renderer import render_fallback_message

        result = render_fallback_message("ExitPlanMode", self.MOCK_FORMAT)
        for v in result.values():
            assert isinstance(v, bytes)
            assert len(v) > 0

    def test_custom_grid(self):
        from cc_streamdeck.renderer import render_fallback_message

        result = render_fallback_message(
            "ExitPlanMode", self.MOCK_FORMAT, grid_cols=5, grid_rows=3
        )
        assert set(result.keys()) == set(range(15))

    def test_open_key_overlay(self):
        from cc_streamdeck.renderer import render_fallback_message

        without = render_fallback_message("ExitPlanMode", self.MOCK_FORMAT)
        with_open = render_fallback_message("ExitPlanMode", self.MOCK_FORMAT, open_key=2)
        # Top-right key should differ when open_key is set
        assert without[2] != with_open[2]
        # OK key (key 5) should be the same
        assert without[5] == with_open[5]


class TestRenderAskQuestionPage:
    MOCK_FORMAT = {
        "size": (80, 80),
        "format": "BMP",
        "flip": (False, True),
        "rotation": 90,
    }

    def test_returns_all_keys(self):
        from cc_streamdeck.renderer import render_ask_question_page

        result = render_ask_question_page(
            options=["A", "B", "C"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
        )
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_returns_bytes(self):
        from cc_streamdeck.renderer import render_ask_question_page

        result = render_ask_question_page(
            options=["A", "B"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
        )
        for v in result.values():
            assert isinstance(v, bytes)
            assert len(v) > 0

    def test_four_options(self):
        from cc_streamdeck.renderer import render_ask_question_page

        result = render_ask_question_page(
            options=["A", "B", "C", "D"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
        )
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_with_navigation(self):
        from cc_streamdeck.renderer import render_ask_question_page

        result = render_ask_question_page(
            options=["A", "B"],
            selected={"A"},
            control_buttons={"back": "Back", "next": "Next"},
            key_image_format=self.MOCK_FORMAT,
            page_info="2/3",
        )
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_confirm_page_no_options(self):
        from cc_streamdeck.renderer import render_ask_question_page

        result = render_ask_question_page(
            options=[],
            selected=set(),
            control_buttons={"back": "Back", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
        )
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_custom_grid(self):
        from cc_streamdeck.renderer import render_ask_question_page

        result = render_ask_question_page(
            options=["A", "B", "C", "D", "E"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
            grid_cols=5, grid_rows=3,
        )
        assert set(result.keys()) == set(range(15))

    def test_bg_color_applied(self):
        """Empty keys use the provided bg_color (instance color)."""
        from cc_streamdeck.renderer import render_ask_question_page

        # 2 options + 2 controls = 2 empty keys (keys 2, 4 on 3x2 grid)
        result = render_ask_question_page(
            options=["A", "B"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
            bg_color="#0A200A",
        )
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}
        # All keys should render as bytes
        for v in result.values():
            assert isinstance(v, bytes)

    def test_page_info_on_cancel_key(self):
        """page_info is rendered in the body area of the cancel/back control key."""
        from cc_streamdeck.renderer import render_ask_question_page

        # 3x2: key 2=cancel (top-right), key 5=next
        result_with = render_ask_question_page(
            options=["A", "B"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "next": "Next"},
            key_image_format=self.MOCK_FORMAT,
            page_info="1/2",
        )
        result_without = render_ask_question_page(
            options=["A", "B"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "next": "Next"},
            key_image_format=self.MOCK_FORMAT,
            page_info="",
        )
        # Cancel key (2, top-right) should differ (has page_info in body)
        assert result_with[2] != result_without[2]
        # Empty key (4) should be same
        assert result_with[4] == result_without[4]

    def test_page_description_on_submit_key(self):
        """page_description is rendered in the body area of the submit/next control key."""
        from cc_streamdeck.renderer import render_ask_question_page

        result_with = render_ask_question_page(
            options=["A", "B"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
            page_description="Which option?",
        )
        result_without = render_ask_question_page(
            options=["A", "B"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "submit": "Submit"},
            key_image_format=self.MOCK_FORMAT,
            page_description="",
        )
        # Submit key (5) should differ (has page_description in body)
        assert result_with[5] != result_without[5]

    def test_page_info_shown_even_with_full_options(self):
        """page_info on control keys works regardless of option count."""
        from cc_streamdeck.renderer import render_ask_question_page

        # 4 options on 3x2: keys 0,1,3,4=options, 2=cancel(top-right), 5=next
        result_with = render_ask_question_page(
            options=["A", "B", "C", "D"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "next": "Next"},
            key_image_format=self.MOCK_FORMAT,
            page_info="1/2",
        )
        result_without = render_ask_question_page(
            options=["A", "B", "C", "D"],
            selected=set(),
            control_buttons={"cancel": "Cancel", "next": "Next"},
            key_image_format=self.MOCK_FORMAT,
            page_info="",
        )
        # Cancel key (2, top-right) should differ — page_info always fits on control key body
        assert result_with[2] != result_without[2]


class TestRenderNotification:
    """Tests for low-priority notification rendering."""

    MOCK_FORMAT = {
        "size": (80, 80),
        "format": "BMP",
        "flip": (False, True),
        "rotation": 90,
    }

    def test_returns_all_keys(self):
        from cc_streamdeck.renderer import render_notification

        result = render_notification("Claude is idle", self.MOCK_FORMAT)
        assert set(result.keys()) == {0, 1, 2, 3, 4, 5}

    def test_returns_bytes(self):
        from cc_streamdeck.renderer import render_notification

        result = render_notification("Claude is idle", self.MOCK_FORMAT)
        for v in result.values():
            assert isinstance(v, bytes)
            assert len(v) > 0

    def test_upper_keys_are_black(self):
        from cc_streamdeck.renderer import render_notification

        # Upper row (keys 0,1,2) should all be identical black
        result = render_notification("Test", self.MOCK_FORMAT)
        assert result[0] == result[1] == result[2]

    def test_custom_grid(self):
        from cc_streamdeck.renderer import render_notification

        result = render_notification(
            "Test", self.MOCK_FORMAT, grid_cols=5, grid_rows=3,
        )
        assert set(result.keys()) == set(range(15))
        # Upper 2 rows (keys 0-9) should be black
        assert result[0] == result[9]

    def test_custom_bg_color(self):
        from cc_streamdeck.renderer import render_notification

        result1 = render_notification("Test", self.MOCK_FORMAT, bg_color="#0A0A20")
        result2 = render_notification("Test", self.MOCK_FORMAT, bg_color="#200A0A")
        # Bottom row should differ with different bg colors
        assert result1[3] != result2[3]

    def test_open_key_overlay(self):
        from cc_streamdeck.renderer import render_notification

        # Top-right key (key 2) should differ when open_key is set
        without = render_notification("Test", self.MOCK_FORMAT)
        with_open = render_notification("Test", self.MOCK_FORMAT, open_key=2)
        assert without[2] != with_open[2]
        # OK key (key 5) should be the same regardless
        assert without[5] == with_open[5]


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
