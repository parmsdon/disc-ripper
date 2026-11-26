"""
Content-based DVD disc ID.

Computes a stable identifier from the VIDEO_TS IFO files, which encode the
disc's navigation structure (title count, sector offsets, menus, etc.). Unlike
the ISO 9660 Volume ID (a free-text authoring label), this ID is unique per
disc content and survives re-pressing and re-ripping.

Hash input (concatenated in order):
  1. VIDEO_TS.IFO with region mask byte (offset 35) zeroed — so a region-patched
     ISO produces the same ID as the original physical disc.
  2. Every VTS_nn_0.IFO, sorted by filename — adds per-title structure info.

Format: "ifo:<first-16-chars-of-MD5>"

Works on /dev/srX devices and .iso files using the same code path: both support
seeking by byte offset once the IFO sector number is known from isoinfo -l.
"""

import hashlib
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# Matches any _0.IFO line in isoinfo -l output, e.g.:
# "----------   0    0    0           22528 Sep 10 2008 [  19199 00]  VIDEO_TS.IFO;1"
# "----------   0    0    0           36864 Sep 10 2008 [  19347 00]  VTS_01_0.IFO;1"
# Captures: (file_size_bytes, sector_number, bare_filename_without_version)
_IFO_LINE_RE = re.compile(
    r'^\S+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+\w+\s+\d+\s+\d+\s+\[\s*(\d+)\s+\d+\]\s+((?:VIDEO_TS|VTS_\d+_0)\.IFO)',
    re.IGNORECASE,
)

# IFO files smaller than this are almost certainly truncated or corrupt.
_MIN_IFO_BYTES = 36  # must cover region byte at offset 35 (VIDEO_TS.IFO only)


def _list_ifo_files(path: str) -> list[tuple[str, int, int]] | None:
    """
    Return a sorted list of (filename, sector, size_bytes) for all IFO files
    that belong to the hash: VIDEO_TS.IFO and all VTS_nn_0.IFO files.
    Returns None on isoinfo failure.
    """
    try:
        proc = subprocess.run(
            ["isoinfo", "-l", "-i", path],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("isoinfo -l failed for %s: %s", path, e)
        return None

    files: list[tuple[str, int, int]] = []
    for line in proc.stdout.splitlines():
        m = _IFO_LINE_RE.match(line.strip())
        if m:
            size, sector, name = int(m.group(1)), int(m.group(2)), m.group(3).upper()
            files.append((name, sector, size))

    if not files:
        logger.warning("No IFO files found in isoinfo -l output for %s", path)
        return None

    # VIDEO_TS.IFO first, then VTS sorted by name (VTS_01, VTS_02, …)
    files.sort(key=lambda t: (0 if t[0] == "VIDEO_TS.IFO" else 1, t[0]))
    return files


def compute_dvd_id(path: str) -> str | None:
    """
    Compute a stable content-based DVD ID.

    path may be a device path (/dev/srX) or an ISO file. Returns
    "ifo:<16-char-hex>" or None if the disc cannot be identified
    (timeout, read error, not a DVD-Video disc).

    Hash input: VIDEO_TS.IFO (region byte at offset 35 zeroed) followed by
    each VTS_nn_0.IFO in sorted order, all concatenated into one MD5.
    """
    ifo_files = _list_ifo_files(path)
    if ifo_files is None:
        return None

    h = hashlib.md5()

    try:
        with open(path, "rb") as f:
            for name, sector, size in ifo_files:
                if size < _MIN_IFO_BYTES:
                    logger.warning(
                        "%s in %s is only %d bytes — too small, skipping",
                        name, path, size,
                    )
                    continue
                f.seek(sector * 2048)
                content = f.read(size)
                if len(content) < size:
                    logger.warning(
                        "Short read of %s from %s: expected %d, got %d",
                        name, path, size, len(content),
                    )
                    continue
                if name == "VIDEO_TS.IFO":
                    # Zero the region mask byte so a region-patched ISO or
                    # re-rip of a patched disc hashes identically to the original.
                    buf = bytearray(content)
                    buf[35] = 0x00
                    content = bytes(buf)
                h.update(content)
    except OSError as e:
        logger.warning("Failed to read IFO content from %s: %s", path, e)
        return None

    return "ifo:" + h.hexdigest()[:16]
