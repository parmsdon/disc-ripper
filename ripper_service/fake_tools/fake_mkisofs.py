"""
Stand-in for `mkisofs` used when fake_rip_mode is on. Mimics mkisofs's
percentage-done output format and writes a small dummy file at the
output path - doesn't need to be a real ISO, just needs to exist.

Usage: python3 -m ripper_service.fake_tools.fake_mkisofs -o <output.iso> <source_dir>
"""

import argparse
import sys
import time

_STEPS = (10, 30, 50, 70, 90, 100)
_STEP_SECONDS = 8 / len(_STEPS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", dest="output_iso", required=True)
    parser.add_argument("source_dir")
    args, _unknown = parser.parse_known_args()

    for pct in _STEPS:
        print(f" {pct:.2f}% done, estimate finish fake_mkisofs", flush=True)
        time.sleep(_STEP_SECONDS)

    with open(args.output_iso, "wb") as f:
        f.write(b"\x00" * 2048)

    print("Total translation table size: 0", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
