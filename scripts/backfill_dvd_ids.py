"""
One-time backfill: compute content-based IFO fingerprints for all ripped / imported DVDs.

For every DVD disc with a raw_path that points to an ISO file, runs compute_dvd_id()
and updates disc_fingerprint to "ifo:<hex>" if the computed ID differs from the stored one.

If the new ID matches another disc already in the database, both discs are flagged with
possible_duplicate_of_id rather than merged — the user decides which to delete.

Usage:
    DISCRIPPER_ENV=dev  python3 scripts/backfill_dvd_ids.py [--dry-run]
    DISCRIPPER_ENV=prod python3 scripts/backfill_dvd_ids.py [--dry-run]

NOTE ON DVDBACKUP ISOs:
  dvdbackup copies individual files and mkisofs rebuilds the ISO structure. The
  volume space size in the resulting ISO may differ slightly from the original disc
  (mkisofs computes its own sector layout). If so, compute_dvd_id() on the ISO will
  return a different value than compute_dvd_id() on the physical device. This means:
  - The backfilled fingerprint will be stable for ISO→ISO comparisons and
    correctly distinguishes discs with the same generic Volume ID.
  - A future re-insertion of the same physical disc will compute the device-based ID.
    If that differs from the backfilled ISO-based ID, the system will create a new
    disc row and show a "possible duplicate" flag in the Audit tab.
  MakeMKV (backup --decrypt) is a bit-for-bit copy, so device ID == ISO ID there.
  Verify by running compute_dvd_id on a known ISO and then on the same disc in the
  drive before relying on backfilled fingerprints for dedup.
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import get_db_url, load_config
from common.models import Disc, DiscType
from ripper_service.disc_id import compute_dvd_id

BATCH_SIZE = 50


def find_iso(datastore_root: str, raw_path: str) -> Path | None:
    """Return the first .iso file inside dvd_store/raw/<disc_id>/."""
    iso_dir = Path(datastore_root) / raw_path
    if not iso_dir.is_dir():
        return None
    isos = sorted(iso_dir.glob("*.iso"))
    return isos[0] if isos else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill IFO-based DVD fingerprints")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()

    env = os.environ.get("DISCRIPPER_ENV", "dev")
    cfg = load_config(env)
    db_url = get_db_url(cfg)
    datastore_root = cfg["storage"]["datastore_root"]

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    counts = {
        "total": 0,
        "no_iso": 0,
        "id_failed": 0,
        "already_current": 0,
        "updated": 0,
        "flagged_duplicate": 0,
    }

    with Session() as session:
        discs = session.scalars(
            select(Disc)
            .where(Disc.type == DiscType.dvd, Disc.raw_path.isnot(None))
            .order_by(Disc.id)
        ).all()

        pending: list[tuple[Disc, str]] = []

        for disc in discs:
            counts["total"] += 1
            iso_path = find_iso(datastore_root, disc.raw_path)
            if iso_path is None:
                print(f"  disc #{disc.id:5d}: SKIP — no ISO in {disc.raw_path}")
                counts["no_iso"] += 1
                continue

            new_id = compute_dvd_id(str(iso_path))
            if new_id is None:
                print(f"  disc #{disc.id:5d}: SKIP — compute_dvd_id failed for {iso_path.name}")
                counts["id_failed"] += 1
                continue

            if disc.disc_fingerprint == new_id:
                counts["already_current"] += 1
                continue

            old = disc.disc_fingerprint or "NULL"
            print(f"  disc #{disc.id:5d}: {old!r:50s} → {new_id!r}  [{iso_path.name}]")
            pending.append((disc, new_id))

            if len(pending) >= BATCH_SIZE:
                _apply_batch(session, pending, counts, args.dry_run)
                pending.clear()

        if pending:
            _apply_batch(session, pending, counts, args.dry_run)

        if not args.dry_run:
            session.commit()

    print()
    print("Summary")
    print(f"  Total DVDs with raw_path:  {counts['total']}")
    print(f"  No ISO found:              {counts['no_iso']}")
    print(f"  compute_dvd_id failed:     {counts['id_failed']}")
    print(f"  Already up to date:        {counts['already_current']}")
    print(f"  Updated:                   {counts['updated']}")
    print(f"  Flagged as duplicate:      {counts['flagged_duplicate']}")
    if args.dry_run:
        print("  (dry-run — no changes written)")


def _apply_batch(
    session,
    pending: list[tuple[Disc, str]],
    counts: dict,
    dry_run: bool,
) -> None:
    for disc, new_id in pending:
        # Check whether another disc already has this fingerprint.
        other = session.scalar(
            select(Disc).where(
                Disc.disc_fingerprint == new_id,
                Disc.id != disc.id,
            ).limit(1)
        )
        if other is not None:
            print(
                f"    → disc #{disc.id} new ID {new_id!r} matches disc #{other.id} "
                f"({other.disc_fingerprint!r}) — flagging both as possible duplicates"
            )
            counts["flagged_duplicate"] += 1
            if not dry_run:
                disc.disc_fingerprint = new_id
                disc.possible_duplicate_of_id = other.id
                if other.possible_duplicate_of_id is None:
                    other.possible_duplicate_of_id = disc.id
        else:
            counts["updated"] += 1
            if not dry_run:
                disc.disc_fingerprint = new_id


if __name__ == "__main__":
    main()
