"""
Content-based DVD disc ID.

Computes a stable identifier from the VIDEO_TS IFO files, which encode the
disc's navigation structure (title count, sector offsets, menus, etc.). Unlike
the ISO 9660 Volume ID (a free-text authoring label), this ID is unique per
disc content and survives re-pressing and re-ripping.

Hash input (MD5, concatenated in order):
  1. VIDEO_TS.IFO with region mask byte (offset 35) zeroed — so a region-patched
     ISO produces the same ID as the original physical disc.
  2. Every VTS_nn_0.IFO, in title-set order (VTS_01, VTS_02, …)

Format: "ifo:<first-16-chars-of-MD5>"

Primary path: libdvdread (ctypes → libdvdread.so.8).  libdvdread reads through
both the ISO 9660 and UDF filesystems, so it handles protected discs where
isoinfo (ISO 9660 only) finds no directory entries.

Fallback path: isoinfo.  Used when libdvdread is unavailable and for the
verification script.  Produces an identical hash on discs where both methods
work (confirmed on Sleeping Beauty: ifo:8c690a14d030a797 from both paths).
"""

import hashlib
import logging
import re
import struct
import subprocess
import ctypes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# libdvdread ctypes interface
# ---------------------------------------------------------------------------

DVD_READ_INFO_FILE = 0   # domain: .IFO files

# VTS count in VIDEO_TS.IFO: big-endian uint16_t at offset 0x3E (62).
# Defined in libdvdread's ifo_types.h as vmg_nr_of_title_sets.
_VTS_COUNT_OFFSET = 0x3E

# Region mask byte in VIDEO_TS.IFO: offset 35 (0x23) within the
# vmg_category field. Zeroed before hashing so region-patched ISOs
# produce the same ID as the original disc. VTS IFOs have no region byte.
_REGION_BYTE_OFFSET = 35


def _load_libdvdread():
    """Return a configured ctypes handle to libdvdread, or None."""
    for name in ("libdvdread.so.8", "libdvdread.so.4", "libdvdread.so"):
        try:
            lib = ctypes.CDLL(name)
            lib.DVDOpen.restype       = ctypes.c_void_p
            lib.DVDOpen.argtypes      = [ctypes.c_char_p]
            lib.DVDOpenFile.restype   = ctypes.c_void_p
            lib.DVDOpenFile.argtypes  = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            lib.DVDReadBytes.restype  = ctypes.c_ssize_t
            lib.DVDReadBytes.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
            lib.DVDFileSize.restype   = ctypes.c_ssize_t
            lib.DVDFileSize.argtypes  = [ctypes.c_void_p]
            lib.DVDCloseFile.restype  = None
            lib.DVDCloseFile.argtypes = [ctypes.c_void_p]
            lib.DVDClose.restype      = None
            lib.DVDClose.argtypes     = [ctypes.c_void_p]
            return lib
        except OSError:
            continue
    return None


_lib = _load_libdvdread()


def _read_ifo_via_libdvdread(path: str) -> bytes | None:
    """
    Hash all IFO files from path using libdvdread.

    Returns the raw MD5 hexdigest bytes (not yet formatted), or None on failure.
    libdvdread reads through UDF as well as ISO 9660, so it works on discs
    where isoinfo finds no directory entries.
    """
    if _lib is None:
        return None

    dvd = _lib.DVDOpen(path.encode())
    if not dvd:
        logger.warning("libdvdread: DVDOpen failed for %s", path)
        return None

    try:
        h = hashlib.md5()

        # --- VIDEO_TS.IFO (titlenum = 0) ---
        f = _lib.DVDOpenFile(dvd, 0, DVD_READ_INFO_FILE)
        if not f:
            logger.warning("libdvdread: could not open VIDEO_TS.IFO from %s", path)
            return None
        try:
            n_sectors = _lib.DVDFileSize(f)
            if n_sectors <= 0:
                logger.warning("libdvdread: VIDEO_TS.IFO size=0 in %s", path)
                return None
            buf = ctypes.create_string_buffer(n_sectors * 2048)
            n_read = _lib.DVDReadBytes(f, buf, n_sectors * 2048)
            if n_read <= 0:
                logger.warning("libdvdread: DVDReadBytes returned %d for VIDEO_TS.IFO in %s", n_read, path)
                return None
            # Read VTS count before zeroing the region byte.
            if n_read < _VTS_COUNT_OFFSET + 2:
                logger.warning("libdvdread: VIDEO_TS.IFO too small (%d bytes) in %s", n_read, path)
                return None
            vts_count = struct.unpack_from(">H", buf.raw, _VTS_COUNT_OFFSET)[0]
            # Zero the region mask byte.
            content = bytearray(buf.raw[:n_read])
            content[_REGION_BYTE_OFFSET] = 0x00
            h.update(bytes(content))
        finally:
            _lib.DVDCloseFile(f)

        # --- VTS_01_0.IFO … VTS_nn_0.IFO ---
        for i in range(1, vts_count + 1):
            f = _lib.DVDOpenFile(dvd, i, DVD_READ_INFO_FILE)
            if not f:
                logger.warning("libdvdread: could not open VTS_%02d_0.IFO from %s", i, path)
                continue
            try:
                n_sectors = _lib.DVDFileSize(f)
                if n_sectors <= 0:
                    continue
                buf = ctypes.create_string_buffer(n_sectors * 2048)
                n_read = _lib.DVDReadBytes(f, buf, n_sectors * 2048)
                if n_read > 0:
                    h.update(buf.raw[:n_read])
            finally:
                _lib.DVDCloseFile(f)

        return h.hexdigest()
    finally:
        _lib.DVDClose(dvd)


# ---------------------------------------------------------------------------
# isoinfo fallback
# ---------------------------------------------------------------------------

# Matches VIDEO_TS.IFO and VTS_nn_0.IFO lines in isoinfo -l output, e.g.:
# "----------   0    0    0           22528 Sep 10 2008 [  19199 00]  VIDEO_TS.IFO;1"
# Captures: (file_size_bytes, sector_number, bare_filename)
_IFO_LINE_RE = re.compile(
    r'^\S+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+\w+\s+\d+\s+\d+\s+\[\s*(\d+)\s+\d+\]\s+((?:VIDEO_TS|VTS_\d+_0)\.IFO)',
    re.IGNORECASE,
)

_MIN_IFO_BYTES = 36  # must cover region byte at offset 35


def _list_ifo_files(path: str) -> list[tuple[str, int, int]] | None:
    """
    Return a sorted list of (filename, sector, size_bytes) for all IFO files.
    Uses isoinfo -l; returns None on failure.  ISO 9660 only — use libdvdread
    for discs where isoinfo finds nothing.
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
        logger.debug("isoinfo -l found no IFO files for %s (possibly UDF-only disc)", path)
        return None

    files.sort(key=lambda t: (0 if t[0] == "VIDEO_TS.IFO" else 1, t[0]))
    return files


def _read_ifo_via_isoinfo(path: str) -> str | None:
    """
    Hash all IFO files from path using isoinfo (ISO 9660 only).
    Returns hexdigest or None on failure.
    """
    ifo_files = _list_ifo_files(path)
    if ifo_files is None:
        return None

    h = hashlib.md5()

    try:
        with open(path, "rb") as f:
            for name, sector, size in ifo_files:
                if size < _MIN_IFO_BYTES:
                    logger.warning("%s in %s is only %d bytes — skipping", name, path, size)
                    continue
                f.seek(sector * 2048)
                content = f.read(size)
                if len(content) < size:
                    logger.warning("Short read of %s from %s: expected %d, got %d",
                                   name, path, size, len(content))
                    continue
                if name == "VIDEO_TS.IFO":
                    buf = bytearray(content)
                    buf[_REGION_BYTE_OFFSET] = 0x00
                    content = bytes(buf)
                h.update(content)
    except OSError as e:
        logger.warning("Failed to read IFO content from %s: %s", path, e)
        return None

    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_dvd_id(path: str) -> str | None:
    """
    Compute a stable content-based DVD ID.

    path may be a device path (/dev/srX) or an ISO file. Returns
    "ifo:<16-char-hex>" or None if the disc cannot be identified
    (timeout, read error, not a DVD-Video disc).

    Tries libdvdread first (handles both ISO 9660 and UDF), falls back
    to isoinfo (ISO 9660 only).
    """
    hexdigest = _read_ifo_via_libdvdread(path)
    if hexdigest is not None:
        return "ifo:" + hexdigest[:16]

    logger.debug("libdvdread path failed for %s — trying isoinfo fallback", path)
    hexdigest = _read_ifo_via_isoinfo(path)
    if hexdigest is not None:
        return "ifo:" + hexdigest[:16]

    logger.warning("compute_dvd_id: both libdvdread and isoinfo failed for %s", path)
    return None
