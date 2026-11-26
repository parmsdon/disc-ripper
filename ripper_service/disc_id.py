"""
Content-based DVD disc ID.

Computes a stable identifier from the VIDEO_TS.IFO file, which encodes the
disc's navigation structure (title count, sector offsets, menus). Unlike the
ISO 9660 Volume ID (which is a free-text authoring label), this ID is unique
per disc content and survives re-pressing and re-ripping.

The region mask byte at offset 35 in VIDEO_TS.IFO is zeroed before hashing,
so a region-patched ISO produces the same ID as the original physical disc.

Format: "ifo:<first-16-chars-of-MD5>"

Works on /dev/srX devices and .iso files using the same code path: both
support seeking by byte offset once the VIDEO_TS.IFO sector is known.
"""

import hashlib
import logging
import re
import struct
import subprocess

logger = logging.getLogger(__name__)

# Matches the sector and file size from an isoinfo -l line, e.g.:
# "----------   0    0    0           22528 Sep 10 2008 [  19199 00]  VIDEO_TS.IFO;1"
_IFO_LINE_RE = re.compile(
    r'^\S+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+\w+\s+\d+\s+\d+\s+\[\s*(\d+)\s+\d+\]\s+VIDEO_TS\.IFO',
    re.IGNORECASE,
)
_VOLUME_SIZE_RE = re.compile(r'Volume size is:\s*(\d+)', re.IGNORECASE)

# IFO files smaller than this are almost certainly truncated or corrupt.
_MIN_IFO_BYTES = 36  # must cover the region byte at offset 35


def _get_volume_space_size(path: str) -> int | None:
    """Return the Volume Space Size (total 2048-byte sectors) from isoinfo -d."""
    try:
        proc = subprocess.run(
            ["isoinfo", "-d", "-i", path],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("isoinfo -d failed for %s: %s", path, e)
        return None
    m = _VOLUME_SIZE_RE.search(proc.stdout)
    if not m:
        logger.warning("Could not parse volume size from isoinfo -d for %s", path)
        return None
    return int(m.group(1))


def _find_ifo_sector_and_size(path: str) -> tuple[int, int] | tuple[None, None]:
    """
    Return (sector, file_size_bytes) for VIDEO_TS.IFO, or (None, None) on failure.
    Uses isoinfo -l to locate the file in the ISO 9660 directory.
    """
    try:
        proc = subprocess.run(
            ["isoinfo", "-l", "-i", path],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("isoinfo -l failed for %s: %s", path, e)
        return None, None
    for line in proc.stdout.splitlines():
        m = _IFO_LINE_RE.match(line.strip())
        if m:
            return int(m.group(2)), int(m.group(1))
    logger.warning("VIDEO_TS.IFO not found in isoinfo -l for %s", path)
    return None, None


def compute_dvd_id(path: str) -> str | None:
    """
    Compute a stable content-based DVD ID.

    path may be a device path (/dev/srX) or an ISO file. Returns
    "ifo:<16-char-hex>" or None if the disc cannot be identified
    (timeout, read error, not a DVD-Video disc).
    """
    vol_size = _get_volume_space_size(path)
    if vol_size is None:
        return None

    sector, ifo_size = _find_ifo_sector_and_size(path)
    if sector is None:
        return None

    if ifo_size < _MIN_IFO_BYTES:
        logger.warning(
            "VIDEO_TS.IFO at sector %d in %s is only %d bytes — too small",
            sector, path, ifo_size,
        )
        return None

    try:
        with open(path, "rb") as f:
            f.seek(sector * 2048)
            ifo_content = f.read(ifo_size)
    except OSError as e:
        logger.warning("Failed to read VIDEO_TS.IFO from %s: %s", path, e)
        return None

    if len(ifo_content) < _MIN_IFO_BYTES:
        logger.warning(
            "Short read of VIDEO_TS.IFO from %s: expected %d, got %d",
            path, ifo_size, len(ifo_content),
        )
        return None

    # Zero the region mask byte so a region-patched ISO matches the original disc.
    ifo_mut = bytearray(ifo_content)
    ifo_mut[35] = 0x00

    h = hashlib.md5()
    h.update(bytes(ifo_mut))
    h.update(struct.pack(">Q", vol_size))  # volume space size as 8-byte big-endian
    return "ifo:" + h.hexdigest()[:16]
