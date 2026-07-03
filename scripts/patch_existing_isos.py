"""
One-off script: run patch_region_if_needed against all existing ISOs in
dvd_store/raw/ and update disc.ripped_in_region in the DB.

Usage:
    DISCRIPPER_ENV=dev python3 scripts/patch_existing_isos.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.config import load_config, get_db_url
from common.models import Disc
from ripper_service.region_patcher import patch_region_if_needed

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main():
    env = os.environ.get("DISCRIPPER_ENV", "dev")
    cfg = load_config(env)

    if cfg["environment"] != "dev":
        print("ERROR: This script is dev-only. Set DISCRIPPER_ENV=dev.")
        sys.exit(1)

    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()

    raw_dir = Path(cfg["storage"]["datastore_root"]) / "dvd_store" / "raw"
    if not raw_dir.exists():
        print(f"dvd_store/raw not found at {raw_dir}")
        sys.exit(1)

    isos = sorted(raw_dir.glob("*/*.iso"))
    if not isos:
        print("No ISOs found.")
        return

    print(f"Found {len(isos)} ISO(s) in {raw_dir}\n")

    patched = skipped = errors = already_ok = 0

    for iso_path in isos:
        disc_id_str = iso_path.parent.name
        try:
            disc_id = int(disc_id_str)
        except ValueError:
            print(f"  SKIP {iso_path.name} — non-numeric parent dir '{disc_id_str}'")
            skipped += 1
            continue

        disc = session.get(Disc, disc_id)
        if disc is None:
            print(f"  SKIP {iso_path.name} — disc #{disc_id} not in DB")
            skipped += 1
            continue

        print(f"  {iso_path.name} (disc #{disc_id}) ...", end=" ", flush=True)
        original_region = patch_region_if_needed(str(iso_path), disc_id)

        if original_region is None:
            print("skipped (too small or isoinfo failed)")
            skipped += 1
            continue

        region_str = f"0x{original_region:02X}"
        disc.ripped_in_region = region_str
        session.commit()

        region_2_excluded = (original_region >> 1) & 1
        if region_2_excluded:
            print(f"patched {region_str} → 0x00, ripped_in_region set to {region_str}")
            patched += 1
        else:
            print(f"already region 2 compatible ({region_str}), ripped_in_region set to {region_str}")
            already_ok += 1

    session.close()
    print(f"\nDone. patched={patched} already_ok={already_ok} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    main()
