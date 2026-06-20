"""
Eject helper - ejects the disc tray for a given device path.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


def eject_drive(device_path: str) -> bool:
    """
    Eject the disc tray at device_path via the `eject` command.

    Returns True on success (exit 0), False otherwise. Never raises.
    """
    try:
        proc = subprocess.run(
            ["eject", device_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Failed to run eject for %s: %s", device_path, exc)
        return False

    if proc.returncode != 0:
        logger.warning(
            "eject exited %d for %s: %s",
            proc.returncode, device_path, proc.stderr.strip(),
        )
        return False

    return True
