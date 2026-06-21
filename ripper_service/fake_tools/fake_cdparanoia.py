"""
Stand-in for `cdparanoia -e` used when fake_rip_mode is on, for fast
iteration without real optical hardware. Mimics cdparanoia's -e callback
format ("##: <code> [<op>] @ <byte>", as observed against a real drive)
and writes a small dummy WAV file so downstream code has something real
to work with.

Usage: python3 -m ripper_service.fake_tools.fake_cdparanoia -d <device> -t <track> -o <output.wav>
(-d is accepted but ignored - no real device access needed)
"""

import argparse
import sys
import time

_STEPS = 12
_STEP_SECONDS = 6 / _STEPS
_DEFAULT_BYTES_PER_STEP = 31752  # roughly matches a real cdparanoia callback's read chunk size


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
        help="Scale the simulated byte counter to this track's real expected size, for a believable percentage",
    )
    args, _unknown = parser.parse_known_args()

    bytes_per_step = (args.total_bytes // _STEPS) if args.total_bytes else _DEFAULT_BYTES_PER_STEP

    for step in range(1, _STEPS + 1):
        op = "skip" if args.dirty and step == _STEPS // 2 else "read"
        print(f"##: 0 [{op}] @ {step * bytes_per_step}", flush=True)
        time.sleep(_STEP_SECONDS)

    with open(args.output_wav, "wb") as f:
        f.write(b"\x00" * 2048)

    print(f"Done ripping track {args.track_number}.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
