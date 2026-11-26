"""
Verify that compute_dvd_id returns the same value for a physical disc in
the drive and its ripped ISO.

Usage (run on the ripper machine as the ripper user):

    DISCRIPPER_ENV=prod python3 scripts/verify_disc_id.py /dev/sr0 /path/to/disc.iso

The script tries libdvdread (primary) and isoinfo (fallback) independently for
each path and reports which succeeded, then checks whether the final IDs match.
Run this for one dvdbackup ISO and one MakeMKV ISO before the prod backfill.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ripper_service.disc_id import (
    _lib,
    _read_ifo_via_libdvdread,
    _read_ifo_via_isoinfo,
    compute_dvd_id,
)


def _probe(path: str, label: str) -> str | None:
    print(f"  {label}: {path!r}")
    if _lib is None:
        print(f"    libdvdread: NOT AVAILABLE")
    else:
        hex_lib = _read_ifo_via_libdvdread(path)
        if hex_lib:
            print(f"    libdvdread: ifo:{hex_lib[:16]}  ✓")
        else:
            print(f"    libdvdread: failed")

    hex_iso = _read_ifo_via_isoinfo(path)
    if hex_iso:
        print(f"    isoinfo:    ifo:{hex_iso[:16]}  ✓")
    else:
        print(f"    isoinfo:    failed (possibly UDF-only — OK if libdvdread worked)")

    final = compute_dvd_id(path)
    if final:
        print(f"    final ID:   {final}")
    else:
        print(f"    final ID:   NONE — both methods failed")
    return final


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: verify_disc_id.py <device_path> <iso_path>", file=sys.stderr)
        print("Example: verify_disc_id.py /dev/sr0 /mnt/datastore/dvd_store/raw/42/MOVIE.iso")
        sys.exit(1)

    device_path, iso_path = sys.argv[1], sys.argv[2]

    print("Device:")
    device_id = _probe(device_path, "device")

    print("ISO:")
    iso_id = _probe(iso_path, "iso   ")

    print()
    if device_id is None or iso_id is None:
        print("RESULT: INCOMPLETE — one or both IDs could not be computed.")
        sys.exit(2)
    elif device_id == iso_id:
        print(f"RESULT: MATCH  ✓  {device_id}")
        print("        OK to proceed with backfill.")
    else:
        print(f"RESULT: MISMATCH  ✗")
        print(f"        Device: {device_id}")
        print(f"        ISO:    {iso_id}")
        print("        Do NOT run the backfill until this is understood.")
        sys.exit(1)


if __name__ == "__main__":
    main()
