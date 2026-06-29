"""
Stand-in for `cdparanoia -e` used when fake_rip_mode is on, for fast
iteration without real optical hardware. Mimics cdparanoia's real stderr
output format: header lines followed by PROGRESS lines.

Usage: python3 -m ripper_service.fake_tools.fake_cdparanoia -d <device> -t <track> -o <output.wav>
(-d is accepted but ignored - no real device access needed)
"""

import argparse
import sys
import time

_STEPS = 12
_STEP_SECONDS = 6 / _STEPS
_FAKE_START_SECTOR = 0
_BYTES_PER_SECTOR = 2352
_DEFAULT_SECTORS = 20000   # fallback when --total-bytes is 0


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", dest="device_path", default=None)
    parser.add_argument("-t", dest="track_number", required=True, type=int)
    parser.add_argument("-o", dest="output_wav", required=True)
    parser.add_argument(
        "--dirty", action="store_true",
        help="Simulate one skipped/uncorrectable block partway through, then finish successfully anyway",
    )
    parser.add_argument(
        "--total-bytes", dest="total_bytes", type=int, default=0,
        help="Scale the simulated sector count to this track's real expected size",
    )
    args, _unknown = parser.parse_known_args()

    # cdparanoia uses 2352-byte sectors; derive a fake sector/byte count.
    sector_count = (args.total_bytes // _BYTES_PER_SECTOR) if args.total_bytes else _DEFAULT_SECTORS
    total_track_bytes = sector_count * _BYTES_PER_SECTOR
    end_sector = _FAKE_START_SECTOR + sector_count
    bytes_per_step = total_track_bytes // _STEPS

    # Emit the same header lines cdparanoia writes to stderr.
    _err(f"Ripping from sector {_FAKE_START_SECTOR:>7} (track {args.track_number:>2} [0:00.00])")
    _err(f"          to sector {end_sector:>7} (track {args.track_number:>2} [fake])")

    # Emit progress in cdparanoia -e callback format: "##: 0 [op] @ byte_offset".
    # byte_offset is track-relative (0-based), matching how rip_worker.py interprets it.
    for step in range(1, _STEPS + 1):
        op = "skip" if args.dirty and step == _STEPS // 2 else "read"
        byte_offset = step * bytes_per_step
        _err(f"##: 0 [{op}] @ {byte_offset}")
        time.sleep(_STEP_SECONDS)

    with open(args.output_wav, "wb") as f:
        f.write(b"\x00" * 2048)

    _err(f"Done ripping track {args.track_number}.")
    sys.exit(0)


if __name__ == "__main__":
    main()
