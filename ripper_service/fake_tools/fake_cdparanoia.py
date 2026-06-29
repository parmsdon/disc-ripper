"""
Stand-in for `cdparanoia` used when fake_rip_mode is on, for fast
iteration without real optical hardware. Mimics cdparanoia's real stderr
output format: header lines followed by visual PROGRESS lines.

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


def _err(msg: str, end: str = "\n") -> None:
    sys.stderr.write(msg + end)
    sys.stderr.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", dest="device_path", default=None)
    parser.add_argument("-w", action="store_true")  # accepted, ignored
    parser.add_argument("-t", dest="track_number", required=True, type=int)
    parser.add_argument("-o", dest="output_wav", required=True)
    parser.add_argument(
        "--dirty", action="store_true",
        help="Simulate a read error partway through, then finish successfully anyway",
    )
    parser.add_argument(
        "--total-bytes", dest="total_bytes", type=int, default=0,
        help="Scale the simulated sector count to this track's real expected size",
    )
    args, _unknown = parser.parse_known_args()

    sector_count = (args.total_bytes // _BYTES_PER_SECTOR) if args.total_bytes else _DEFAULT_SECTORS
    end_sector = _FAKE_START_SECTOR + sector_count
    sectors_per_step = sector_count // _STEPS

    # Emit the same header lines cdparanoia writes to stderr.
    _err(f"Ripping from sector {_FAKE_START_SECTOR:>7} (track {args.track_number:>2} [0:00.00])")
    _err(f"          to sector {end_sector:>7} (track {args.track_number:>2} [fake])")

    dirty_step = _STEPS // 2 if args.dirty else -1

    for step in range(1, _STEPS + 1):
        current_sector = _FAKE_START_SECTOR + step * sectors_per_step
        # Visual PROGRESS line: \r-terminated like the real cdparanoia.
        progress_line = (
            f" (== PROGRESS == [    >                    | {current_sector:06d} 00 ] == :-) O ==)"
        )
        _err(progress_line, end="\r")

        if step == dirty_step:
            # Emit a read error line that dirty-track detection should catch.
            _err(f"\nread error on sector {current_sector} (fake dirty)")

        time.sleep(_STEP_SECONDS)

    # Final newline so the terminal isn't left mid-line.
    _err("")

    with open(args.output_wav, "wb") as f:
        f.write(b"\x00" * 2048)

    _err(f"Done ripping track {args.track_number}.")
    sys.exit(0)


if __name__ == "__main__":
    main()
