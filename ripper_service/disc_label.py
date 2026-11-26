"""
isoinfo-based DVD volume label detection.

Reads the disc's Volume id / Volume set id at detection time, so a disc
can be identified by something more useful than a bare numeric id before
any ripping/encoding metadata exists.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

# Volume IDs that must never be used as the disc's identity or as a filename
# stem. These are authoring defaults ("CDROM" is the mkisofs default when -V
# is omitted; "DVD_VIDEO" and friends are common generic labels). Callers
# should fall back to the ISO filename stem or working title instead.
GENERIC_VOLUME_LABELS: frozenset[str] = frozenset({
    "CDROM",
    "DVD_VIDEO", "DVDVOLUME", "VIDEO_TS", "DVDROM", "DVD",
    "VIDEO", "DISC1", "DISC2", "DISC3", "DISC4",
})


def read_volume_info(device_path: str) -> dict:
    """
    Read Volume id / Volume set id from device_path via `isoinfo -d -i`.

    Returns {"volume_id": str|None, "volume_set_id": str|None}. Empty
    values are treated as None.

    Never raises - on command failure or a missing tool, returns both as
    None and logs a warning.
    """
    result = {"volume_id": None, "volume_set_id": None}

    try:
        proc = subprocess.run(
            ["isoinfo", "-d", "-i", device_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Failed to run isoinfo for %s: %s", device_path, exc)
        return result

    if proc.returncode != 0:
        logger.warning(
            "isoinfo exited %d for %s: %s",
            proc.returncode, device_path, proc.stderr.strip(),
        )
        return result

    for line in proc.stdout.splitlines():
        lower = line.lower()
        if lower.startswith("volume id:"):
            result["volume_id"] = line.split(":", 1)[1].strip() or None
        elif lower.startswith("volume set id:"):
            result["volume_set_id"] = line.split(":", 1)[1].strip() or None

    return result
