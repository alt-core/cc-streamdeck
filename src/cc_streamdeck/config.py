"""Shared constants for cc_streamdeck."""

from pathlib import Path

SOCKET_PATH = Path("/tmp/cc_streamdeck.sock")
LOG_PATH = Path("/tmp/cc_streamdeck.log")

DAEMON_STARTUP_TIMEOUT = 5.0
CONNECT_RETRY_INTERVAL = 0.2
DEVICE_POLL_INTERVAL = 3.0
HOOK_TIMEOUT = 86400  # Hook/daemon response timeout in seconds (24h)
NO_DEVICE_SHUTDOWN_TIMEOUT = 86400  # Auto-shutdown after 24h with no device

KEY_PIXEL_SIZE = (80, 80)
GRID_COLS = 3
GRID_ROWS = 2
