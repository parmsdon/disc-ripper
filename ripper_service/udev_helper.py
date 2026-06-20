"""
udevadm-based drive identity and media detection.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


def get_drive_info(device_path: str) -> dict:
    """
    Query udevadm for the hardware identity and current media state of an
    optical drive.

    Returns a dict:
        hardware_id: str  (ID_SERIAL, else ID_SERIAL_SHORT, else ID_PATH,
                            else device_path as a last resort)
        media_present: bool
        media_type: "dvd" | "cd" | None

    Never raises - if udevadm is missing, exits nonzero, or the device is
    gone, returns all None/False.
    """
    result = {"hardware_id": None, "media_present": False, "media_type": None}

    try:
        proc = subprocess.run(
            ["udevadm", "info", "--query=all", "--name", device_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Failed to run udevadm for %s: %s", device_path, exc)
        return result

    if proc.returncode != 0:
        logger.warning(
            "udevadm exited %d for %s: %s",
            proc.returncode, device_path, proc.stderr.strip(),
        )
        return result

    props = {}
    for line in proc.stdout.splitlines():
        if not line.startswith("E: ") or "=" not in line:
            continue
        key, _, value = line[len("E: "):].partition("=")
        props[key] = value

    hardware_id = props.get("ID_SERIAL") or props.get("ID_SERIAL_SHORT") or props.get("ID_PATH")
    if not hardware_id:
        hardware_id = device_path
        logger.warning(
            "No stable hardware identifier (ID_SERIAL/ID_SERIAL_SHORT/ID_PATH) for %s - "
            "falling back to device path, which is not stable across reassignment.",
            device_path,
        )
    result["hardware_id"] = hardware_id

    media_present = props.get("ID_CDROM_MEDIA") == "1"
    result["media_present"] = media_present

    if media_present:
        if props.get("ID_CDROM_MEDIA_DVD") == "1":
            result["media_type"] = "dvd"
        elif props.get("ID_CDROM_MEDIA_CD") == "1":
            result["media_type"] = "cd"

    return result
