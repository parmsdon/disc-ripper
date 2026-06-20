"""
Stand-in for `dvdbackup -p -M` used when fake_rip_mode is on, for fast
iteration without real optical hardware. Mimics dvdbackup's -p progress
output format and produces a minimal but real VIDEO_TS directory so
downstream code (mkisofs, etc.) has something real to work with.

Usage: python3 -m ripper_service.fake_tools.fake_dvdbackup -i <device> -o <dir> -n <name>
(-i is accepted but ignored - no real device access needed)
"""

import argparse
import os
import sys
import time

_PARTS = 4
_STEPS = (25, 50, 75, 100)
_STEP_SECONDS = 30 / (_PARTS * len(_STEPS))
_TOTAL_MIB = 1024


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", dest="device_path", default=None)
    parser.add_argument("-o", dest="output_dir", required=True)
    parser.add_argument("-n", dest="name", required=True)
    args, _unknown = parser.parse_known_args()

    for part in range(1, _PARTS + 1):
        for pct in _STEPS:
            done_mib = int(_TOTAL_MIB * pct / 100)
            print(
                f"Copying Title, part {part}/{_PARTS}: {pct}% done ({done_mib}/{_TOTAL_MIB} MiB)",
                flush=True,
            )
            time.sleep(_STEP_SECONDS)

    video_ts_dir = os.path.join(args.output_dir, args.name, "VIDEO_TS")
    os.makedirs(video_ts_dir, exist_ok=True)

    open(os.path.join(video_ts_dir, "VIDEO_TS.IFO"), "wb").close()
    with open(os.path.join(video_ts_dir, "VTS_01_0.VOB"), "wb") as f:
        f.write(b"\x00" * 2048)

    print("DVD backup done.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
