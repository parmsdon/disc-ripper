"""
CDROM_DRIVE_STATUS ioctl-based tray status detection.

udevadm alone can't distinguish "tray open" from "tray closed, no disc"
from "disc present" - this ioctl can.
"""

import fcntl
import logging
import os

logger = logging.getLogger(__name__)

CDROM_DRIVE_STATUS = 0x5326

_STATUS_NAMES = {
    0: "no_info",
    1: "no_disc",
    2: "tray_open",
    3: "not_ready",
    4: "disc_ok",
}


def get_tray_status(device_path: str) -> str:
    """
    Query the CDROM_DRIVE_STATUS ioctl for device_path.

    Returns one of: "no_info", "no_disc", "tray_open", "not_ready",
    "disc_ok", or "unknown" if the status couldn't be determined (device
    busy, permission error, doesn't exist, etc).

    Never raises.
    """
    fd = None
    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
        status = fcntl.ioctl(fd, CDROM_DRIVE_STATUS)
        return _STATUS_NAMES.get(status, "unknown")
    except Exception as exc:
        logger.warning("Failed to read tray status for %s: %s", device_path, exc)
        return "unknown"
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
