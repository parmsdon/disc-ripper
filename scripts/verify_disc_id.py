"""
Verify that compute_dvd_id returns the same value for a physical disc in
the drive and its ripped ISO.

Usage (run on the ripper machine as the ripper user):

    DISCRIPPER_ENV=prod python3 scripts/verify_disc_id.py /dev/sr0 /path/to/disc.iso

The script prints the ID computed from each source and reports whether they
match. Use this to confirm the algorithm is consistent before running the
backfill on prod.

Suggested test pairs:
  1. One disc ripped with dvdbackup (mkisofs ISO)
  2. One disc ripped with MakeMKV (backup --decrypt ISO)
  3. One region-patched ISO (verify it still matches the unpatched disc in the drive)
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ripper_service.disc_id import compute_dvd_id, _list_ifo_files


def _summarise(path: str, label: str) -> str | None:
    files = _list_ifo_files(path)
    if files is None:
        print(f"  {label}: FAILED — could not list IFO files from {path!r}")
        return None
    names = ", ".join(n for n, _, _ in files)
    print(f"  {label}: found {len(files)} IFO file(s): {names}")
    disc_id = compute_dvd_id(path)
    if disc_id is None:
        print(f"  {label}: FAILED — compute_dvd_id returned None")
    else:
        print(f"  {label}: {disc_id}")
    return disc_id


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: verify_disc_id.py <device_path> <iso_path>", file=sys.stderr)
        print("Example: verify_disc_id.py /dev/sr0 /mnt/datastore/dvd_store/raw/42/MOVIE.iso")
        sys.exit(1)

    device_path, iso_path = sys.argv[1], sys.argv[2]

    print(f"Device: {device_path}")
    device_id = _summarise(device_path, "device")

    print(f"\nISO:    {iso_path}")
    iso_id = _summarise(iso_path, "iso   ")

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
