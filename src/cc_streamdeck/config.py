"""Shared constants for cc_streamdeck."""

from pathlib import Path

SOCKET_PATH = Path("/tmp/cc_streamdeck.sock")
LOG_PATH = Path("/tmp/cc_streamdeck.log")

DAEMON_STARTUP_TIMEOUT = 5.0
CONNECT_RETRY_INTERVAL = 0.2
DEVICE_POLL_INTERVAL = 3.0

KEY_PIXEL_SIZE = (80, 80)
GRID_COLS = 3
GRID_ROWS = 2
