"""Stream Deck device management with hotplug polling."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config import DEVICE_POLL_INTERVAL, NO_DEVICE_SHUTDOWN_TIMEOUT

logger = logging.getLogger(__name__)

KeyCallback = Callable[..., None]  # (deck, key: int, state: bool) -> None


def _patch_product_ids() -> None:
    """Register product IDs not yet known to the library."""
    from StreamDeck.Devices.StreamDeckMini import StreamDeckMini
    from StreamDeck.ProductIDs import USBProductIDs

    # Map of PID attr name -> (hex value, device class)
    # Add variants not yet in the library here.
    extra_pids = {
        "USB_PID_STREAMDECK_MINI_DISCORD": (0x00B3, StreamDeckMini),  # Mini Discord Edition
    }

    patched = []
    for attr, (pid, _cls) in extra_pids.items():
        if not hasattr(USBProductIDs, attr):
            setattr(USBProductIDs, attr, pid)
            patched.append(f"{attr}=0x{pid:04X}")

    if patched:
        # Patch DeviceManager.enumerate to include the new PIDs
        from StreamDeck.DeviceManager import DeviceManager
        from StreamDeck.ProductIDs import USBVendorIDs

        _orig_enumerate = DeviceManager.enumerate

        def _patched_enumerate(self) -> list:
            devices = _orig_enumerate(self)
            for attr, (pid, cls) in extra_pids.items():
                found = self.transport.enumerate(vid=USBVendorIDs.USB_VID_ELGATO, pid=pid)
                devices.extend([cls(d) for d in found])
            return devices

        DeviceManager.enumerate = _patched_enumerate
        logger.info("Patched StreamDeck ProductIDs: %s", ", ".join(patched))


_patch_product_ids()


class DeviceState:
    """Manages Stream Deck Mini device lifecycle and hotplug detection."""

    def __init__(self) -> None:
        self._deck = None
        self._lock = threading.Lock()
        self._status: str = "no_device"
        self._key_callback: KeyCallback | None = None
        self._poll_thread: threading.Thread | None = None
        self._running = False
        self._no_device_since: float = time.monotonic()

    @property
    def status(self) -> str:
        return self._status

    @property
    def deck(self):
        return self._deck

    def start_polling(self, key_callback: KeyCallback) -> None:
        """Start periodic device enumeration and open device if found."""
        self._key_callback = key_callback
        self._running = True
        self._try_open()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        """Stop polling and close device, restoring Elgato logo."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        self._close_device(reset=True)

    def set_key_images(self, images: dict[int, bytes]) -> None:
        """Set images on the device. Thread-safe.

        On HID error, closes the device and sets status to "no_device"
        so the poll loop can attempt reconnection.
        """
        with self._lock:
            if self._deck is None:
                return
            try:
                with self._deck:
                    for key, img_bytes in images.items():
                        self._deck.set_key_image(key, img_bytes)
            except Exception as e:
                logger.info("HID write failed, closing device: %s", e)
                self._close_device_locked()

    def get_key_image_format(self) -> dict | None:
        """Return the key image format dict, or None if no device."""
        with self._lock:
            if self._deck is None:
                return None
            return self._deck.key_image_format()

    def get_grid_layout(self) -> tuple[int, int, int] | None:
        """Return (rows, cols, key_count) from the device, or None if no device."""
        with self._lock:
            if self._deck is None:
                return None
            rows, cols = self._deck.key_layout()
            return (rows, cols, self._deck.key_count())

    def clear_keys(self) -> None:
        """Clear all keys to black."""
        with self._lock:
            if self._deck is None:
                return
            try:
                self._clear_all_keys(self._deck)
            except Exception as e:
                logger.info("HID write failed during clear, closing device: %s", e)
                self._close_device_locked()

    @property
    def no_device_elapsed(self) -> float:
        """Seconds since device was last connected. 0 if currently connected."""
        if self._status == "ready":
            return 0.0
        return time.monotonic() - self._no_device_since

    def _poll_loop(self) -> None:
        while self._running:
            time.sleep(DEVICE_POLL_INTERVAL)
            if self._status == "no_device":
                if self.no_device_elapsed > NO_DEVICE_SHUTDOWN_TIMEOUT:
                    logger.info(
                        "No device for %d seconds, requesting shutdown",
                        int(self.no_device_elapsed),
                    )
                    self._running = False
                    break
                self._try_open()
            elif self._deck is not None:
                try:
                    self._deck.is_open()
                except Exception:
                    logger.info("Device disconnected")
                    self._close_device()

    def _try_open(self) -> None:
        try:
            from StreamDeck.DeviceManager import DeviceManager

            devices = DeviceManager().enumerate()
            for d in devices:
                d.open()
                d.set_brightness(50)
                self._clear_all_keys(d)
                if self._key_callback:
                    d.set_key_callback(self._key_callback)
                with self._lock:
                    self._deck = d
                    self._status = "ready"
                    self._no_device_since = 0.0  # clear timer
                logger.info("Stream Deck opened: %s (%s)", d.deck_type(), d.get_serial_number())
                return
        except Exception as e:
            logger.debug("Device enumeration failed: %s", e)

    @staticmethod
    def _clear_all_keys(deck) -> None:
        """Set all keys to black."""
        from StreamDeck.ImageHelpers import PILHelper

        black = PILHelper.create_key_image(deck)
        native = PILHelper.to_native_key_format(deck, black)
        with deck:
            for k in range(deck.key_count()):
                deck.set_key_image(k, native)

    def _close_device(self, reset: bool = False) -> None:
        with self._lock:
            self._close_device_locked(reset=reset)

    def _close_device_locked(self, reset: bool = False) -> None:
        """Close the device. Caller must hold self._lock."""
        if self._deck is not None:
            try:
                if reset:
                    self._deck.reset()
                self._deck.close()
            except Exception:
                pass
            self._deck = None
        self._status = "no_device"
        self._no_device_since = time.monotonic()
