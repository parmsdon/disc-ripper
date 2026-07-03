"""
DVD region code detection and region-free patching for ripped ISOs.

The region byte lives at byte offset (VIDEO_TS.IFO sector * 2048 + 35)
inside the ISO. Each bit N (0-indexed from LSB) represents region N+1:
bit=1 means that region is NOT allowed. 0x00 = region free; 0xFE = region
1 only; 0xFD = region 2 only; etc.

isoinfo (provided by the genisoimage package on the ripper machine) is used
to locate the VIDEO_TS.IFO sector from the ISO directory listing.
"""

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

MIN_ISO_SIZE = 100 * 1024 * 1024  # 100 MB — anything smaller is a test/fake ISO

_SECTOR_RE = re.compile(r'\[\s*(\d+)\s+\d+\]')


def is_valid_iso(iso_path: str) -> bool:
    try:
        size = os.path.getsize(iso_path)
    except OSError:
        return False
    if size < MIN_ISO_SIZE:
        logger.warning(
            "ISO %s is too small (%d bytes) - likely a test/fake file, skipping region patch",
            iso_path, size,
        )
        return False
    return True


def _find_video_ts_ifo_sector(iso_path: str) -> int | None:
    try:
        result = subprocess.run(
            ["isoinfo", "-i", iso_path, "-l"],
            capture_output=True, text=True, timeout=60,
        )
        for line in result.stdout.splitlines():
            if "video_ts.ifo" in line.lower():
                m = _SECTOR_RE.search(line)
                if m:
                    return int(m.group(1))
        logger.warning("VIDEO_TS.IFO not found in isoinfo listing for %s", iso_path)
        return None
    except Exception:
        logger.warning("isoinfo failed for %s", iso_path, exc_info=True)
        return None


def read_dvd_region(iso_path: str) -> int | None:
    sector = _find_video_ts_ifo_sector(iso_path)
    if sector is None:
        return None
    byte_offset = sector * 2048 + 35
    try:
        with open(iso_path, "rb") as f:
            f.seek(byte_offset)
            data = f.read(1)
        if not data:
            logger.warning("Could not read region byte from %s at offset %d", iso_path, byte_offset)
            return None
        return data[0]
    except Exception:
        logger.warning("Error reading region byte from %s", iso_path, exc_info=True)
        return None


def patch_region_if_needed(iso_path: str, disc_id: int) -> int | None:
    if not is_valid_iso(iso_path):
        return None

    original_byte = read_dvd_region(iso_path)
    if original_byte is None:
        logger.warning("Could not read region for disc #%d, skipping patch", disc_id)
        return None

    region_2_excluded = (original_byte >> 1) & 1
    sector = _find_video_ts_ifo_sector(iso_path)
    if sector is None:
        # read_dvd_region succeeded, so this shouldn't happen
        logger.warning("Disc #%d: could not re-locate sector for write, skipping patch", disc_id)
        return original_byte

    byte_offset = sector * 2048 + 35

    if region_2_excluded:
        try:
            with open(iso_path, "r+b") as f:
                f.seek(byte_offset)
                f.write(bytes([0x00]))
            logger.info(
                "Disc #%d: region byte was 0x%02X - patched to 0x00 (region free)",
                disc_id, original_byte,
            )
        except Exception:
            logger.warning(
                "Disc #%d: failed to write region patch to %s", disc_id, iso_path, exc_info=True,
            )
    else:
        logger.info(
            "Disc #%d: region byte 0x%02X - region 2 already supported, no patch needed",
            disc_id, original_byte,
        )

    return original_byte
