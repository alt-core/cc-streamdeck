"""Tests for DeviceState with mocked Stream Deck hardware."""

from unittest.mock import MagicMock, patch

from cc_streamdeck.device import DeviceState

MOCK_KEY_FORMAT = {
    "size": (80, 80),
    "format": "BMP",
    "flip": (False, True),
    "rotation": 90,
}


def _make_mock_deck():
    mock_deck = MagicMock()
    mock_deck.deck_type.return_value = "Stream Deck Mini"
    mock_deck.get_serial_number.return_value = "TEST123"
    mock_deck.key_image_format.return_value = MOCK_KEY_FORMAT
    mock_deck.key_count.return_value = 6
    return mock_deck


class TestDeviceState:
    def test_initial_status(self):
        state = DeviceState()
        assert state.status == "no_device"
        assert state.deck is None

    def test_get_key_image_format_no_device(self):
        state = DeviceState()
        assert state.get_key_image_format() is None

    @patch("StreamDeck.DeviceManager.DeviceManager")
    def test_try_open_success(self, mock_dm_cls):
        mock_deck = _make_mock_deck()
        mock_dm_cls.return_value.enumerate.return_value = [mock_deck]

        state = DeviceState()
        callback = MagicMock()
        state._key_callback = callback
        state._try_open()

        mock_deck.open.assert_called_once()
        mock_deck.set_brightness.assert_called_once_with(50)
        # Verify keys were cleared to black (set_key_image called for each key)
        assert mock_deck.set_key_image.call_count == 6
        mock_deck.set_key_callback.assert_called_once_with(callback)
        assert state.status == "ready"

    @patch("StreamDeck.DeviceManager.DeviceManager")
    def test_try_open_no_devices(self, mock_dm_cls):
        mock_dm_cls.return_value.enumerate.return_value = []

        state = DeviceState()
        state._try_open()

        assert state.status == "no_device"

    def test_close_device(self):
        state = DeviceState()
        mock_deck = MagicMock()
        state._deck = mock_deck
        state._status = "ready"

        state._close_device()

        mock_deck.close.assert_called_once()
        assert state.status == "no_device"
        assert state.deck is None

    def test_set_key_images_no_device(self):
        state = DeviceState()
        # Should not raise
        state.set_key_images({0: b"img"})

    def test_clear_keys_no_device(self):
        state = DeviceState()
        # Should not raise
        state.clear_keys()

    def test_clear_keys_with_device(self):
        state = DeviceState()
        mock_deck = _make_mock_deck()
        state._deck = mock_deck

        state.clear_keys()

        # Should set all 6 keys to black images
        assert mock_deck.set_key_image.call_count == 6
