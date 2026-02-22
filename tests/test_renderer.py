"""Tests for renderer module."""

from cc_streamdeck.config import KEY_PIXEL_SIZE
from cc_streamdeck.renderer import (
    compute_layout,
    create_choice_image,
    create_message_tiles,
    extract_display_content,
    load_font,
)


class TestComputeLayout:
    def test_three_choices(self):
        msg_keys, choice_keys = compute_layout(3)
        assert msg_keys == [0, 1, 2]
        assert choice_keys == [3, 4, 5]

    def test_two_choices(self):
        msg_keys, choice_keys = compute_layout(2)
        assert msg_keys == [0, 1, 2, 3]
        assert choice_keys == [4, 5]

    def test_one_choice(self):
        msg_keys, choice_keys = compute_layout(1)
        assert msg_keys == [0, 1, 2, 3, 4]
        assert choice_keys == [5]

    def test_all_keys_covered(self):
        for n in [1, 2, 3]:
            msg_keys, choice_keys = compute_layout(n)
            assert sorted(msg_keys + choice_keys) == [0, 1, 2, 3, 4, 5]


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
    def test_regular(self):
        font = load_font("regular", 20)
        assert font is not None

    def test_bold(self):
        font = load_font("bold", 20)
        assert font is not None

    def test_caching(self):
        f1 = load_font("regular", 20)
        f2 = load_font("regular", 20)
        assert f1 is f2


class TestCreateMessageTiles:
    def test_three_key_tiles(self):
        tiles = create_message_tiles("Bash", {"command": "ls"}, [0, 1, 2])
        assert len(tiles) == 3
        for key in [0, 1, 2]:
            assert tiles[key].size == KEY_PIXEL_SIZE

    def test_four_key_tiles(self):
        tiles = create_message_tiles("Write", {"file_path": "/tmp/test.txt"}, [0, 1, 2, 3])
        assert len(tiles) == 4
        for key in [0, 1, 2, 3]:
            assert tiles[key].size == KEY_PIXEL_SIZE

    def test_all_tiles_have_content(self):
        """All message tiles should have some non-black pixels."""
        tiles = create_message_tiles("Bash", {"command": "rm -rf node_modules"}, [0, 1, 2])
        for key in [0, 1, 2]:
            extrema = tiles[key].getextrema()
            # At least one channel has non-zero max (not all black)
            assert any(ch[1] > 0 for ch in extrema), f"Key {key} is all black"


class TestCreateChoiceImage:
    def test_allow_button(self):
        from cc_streamdeck.protocol import PermissionChoice

        img = create_choice_image(PermissionChoice(label="Allow", behavior="allow"))
        assert img.size == KEY_PIXEL_SIZE

    def test_deny_button(self):
        from cc_streamdeck.protocol import PermissionChoice

        img = create_choice_image(PermissionChoice(label="Deny", behavior="deny"))
        assert img.size == KEY_PIXEL_SIZE

    def test_always_button(self):
        from cc_streamdeck.protocol import PermissionChoice

        img = create_choice_image(
            PermissionChoice(label="Always", behavior="allow", updated_permissions=[{}])
        )
        assert img.size == KEY_PIXEL_SIZE
