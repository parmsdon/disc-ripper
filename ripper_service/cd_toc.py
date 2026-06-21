"""
cd-discid-based CD table-of-contents reading: track count, per-track
offsets/lengths, and a stable CDDB-style disc fingerprint for dedup/
re-rip matching (not for display - see disc_fingerprint on Disc).
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

_EMPTY_RESULT = {"track_count": 0, "tracks": [], "fingerprint": None}

# CDDA frames ("sectors" in cd-discid's own terminology) per second.
_FRAMES_PER_SECOND = 75


def read_table_of_contents(device_path: str) -> dict:
    """
    Read the table of contents from device_path via `cd-discid`.

    Returns {"track_count": int, "tracks": [{"number": int,
    "start_sector": int, "length_sectors": int}, ...], "fingerprint":
    str}. track numbers are 1-based.

    Never raises - on command failure, a missing tool, or unparseable
    output, returns track_count=0/tracks=[]/fingerprint=None and logs a
    warning.
    """
    try:
        proc = subprocess.run(
            ["cd-discid", device_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Failed to run cd-discid for %s: %s", device_path, exc)
        return dict(_EMPTY_RESULT)

    if proc.returncode != 0:
        logger.warning(
            "cd-discid exited %d for %s: %s",
            proc.returncode, device_path, proc.stderr.strip(),
        )
        return dict(_EMPTY_RESULT)

    # Format: <fingerprint> <track_count> <offset1> ... <offsetN> <total_seconds>
    parts = proc.stdout.split()
    if len(parts) < 3:
        logger.warning("Unexpected cd-discid output for %s: %r", device_path, proc.stdout)
        return dict(_EMPTY_RESULT)

    try:
        fingerprint = parts[0]
        track_count = int(parts[1])
        offsets = [int(x) for x in parts[2:2 + track_count]]
        total_seconds = int(parts[2 + track_count])
    except (ValueError, IndexError) as exc:
        logger.warning("Failed to parse cd-discid output for %s (%r): %s", device_path, proc.stdout, exc)
        return dict(_EMPTY_RESULT)

    total_frames = total_seconds * _FRAMES_PER_SECOND
    tracks = []
    for index, start in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else total_frames
        tracks.append({
            "number": index + 1,
            "start_sector": start,
            "length_sectors": end - start,
        })

    return {"track_count": track_count, "tracks": tracks, "fingerprint": fingerprint}
