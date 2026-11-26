"""
One-time backfill: populate volume_label for DVD discs where it is NULL.

This is needed on prod because backfill_dvd_ids.py ran before migration 0027
was deployed, so the migration's SQL backfill (which copies plain disc_fingerprint
values) matched zero rows — every fingerprint was already an ifo: hash.

For every DVD with volume_label IS NULL and a raw_path:
  1. Find the .iso file in dvd_store/raw/<disc_id>/.
  2. Read the volume label from the ISO via isoinfo -d -i.
  3. If isoinfo returns a generic label (e.g. "CDROM"), fall back to the
     ISO filename stem (which was derived from the original disc title).
  4. If isoinfo fails or returns empty, fall back to the filename stem.
  5. If no ISO exists, report as failed and skip.

Usage:
    DISCRIPPER_ENV=prod python3 scripts/backfill_volume_labels.py          # dry-run
    DISCRIPPER_ENV=prod python3 scripts/backfill_volume_labels.py --apply  # write

The app/DB machine (ripper) must have isoinfo available (genisoimage package).
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import get_db_url, load_config
from common.models import Disc, DiscType
from ripper_service.disc_label import read_volume_info, GENERIC_VOLUME_LABELS

BATCH_SIZE = 50

# Well-known phase keys that appear in the summary with fixed labels.
_PHASE_KEYS = ("total", "no_raw_path", "no_iso", "failed")


def find_iso(datastore_root: str, raw_path: str) -> Path | None:
    iso_dir = Path(datastore_root) / raw_path
    if not iso_dir.is_dir():
        return None
    isos = sorted(iso_dir.glob("*.iso"))
    return isos[0] if isos else None


def label_from_iso(iso_path: Path) -> tuple[str | None, str]:
    """
    Try isoinfo -d -i; return (label, source).

    source values:
      "iso"                   — isoinfo returned a valid, non-generic label
      "filename(generic:X)"   — isoinfo returned a generic label X (e.g. CDROM);
                                fell back to ISO filename stem
      "filename"              — isoinfo failed or returned empty; using stem
    """
    info = read_volume_info(str(iso_path))
    raw = info["volume_id"] or info["volume_set_id"]
    if raw and raw not in GENERIC_VOLUME_LABELS:
        return raw, "iso"
    if raw and raw in GENERIC_VOLUME_LABELS:
        return iso_path.stem, f"filename(generic:{raw})"
    # isoinfo returned empty (UDF-only, damaged ISO 9660 header, etc.)
    return iso_path.stem, "filename"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill volume_label for DVD discs")
    parser.add_argument("--apply", action="store_true",
                        help="Write to database (default: dry-run)")
    args = parser.parse_args()
    dry_run = not args.apply

    env = os.environ.get("DISCRIPPER_ENV", "dev")
    cfg = load_config(env)
    db_url = get_db_url(cfg)
    datastore_root = cfg["storage"]["datastore_root"]

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    counts: Counter = Counter()

    col_w = 50  # column width for temp_name in report
    src_w = 28  # column width for source in report (longest: "filename(generic:DVD_VIDEO)")

    print(f"{'ID':>6}  {'temp_name':<{col_w}}  {'source':<{src_w}}  label")
    print("-" * (6 + 2 + col_w + 2 + src_w + 2 + 50))

    pending: list[tuple[Disc, str]] = []

    with Session() as session:
        discs = session.scalars(
            select(Disc)
            .where(
                Disc.type == DiscType.dvd,
                Disc.volume_label.is_(None),
            )
            .order_by(Disc.id)
        ).all()

        for disc in discs:
            counts["total"] += 1
            tname = (disc.temp_name or "")[:col_w]

            if not disc.raw_path:
                print(f"{disc.id:>6}  {tname:<{col_w}}  {'—':<{src_w}}  (no raw_path — skip)")
                counts["no_raw_path"] += 1
                continue

            iso_path = find_iso(datastore_root, disc.raw_path)
            if iso_path is None:
                print(f"{disc.id:>6}  {tname:<{col_w}}  {'failed':<{src_w}}  (no .iso in {disc.raw_path})")
                counts["no_iso"] += 1
                counts["failed"] += 1
                continue

            label, source = label_from_iso(iso_path)
            if label is None:
                print(f"{disc.id:>6}  {tname:<{col_w}}  {'failed':<{src_w}}  (isoinfo returned nothing, no filename fallback)")
                counts["failed"] += 1
                continue

            counts[source] += 1
            print(f"{disc.id:>6}  {tname:<{col_w}}  {source:<{src_w}}  {label}")
            pending.append((disc, label))

            if not dry_run and len(pending) >= BATCH_SIZE:
                _apply_batch(session, pending)
                pending.clear()

        if not dry_run and pending:
            _apply_batch(session, pending)

        if not dry_run:
            session.commit()

    # Source counts: everything in counts that isn't a phase key.
    source_counts = {k: v for k, v in counts.items() if k not in _PHASE_KEYS}

    print()
    print("Summary")
    print(f"  DVDs with volume_label NULL:  {counts['total']}")
    print(f"  No raw_path (queued/error):   {counts['no_raw_path']}")
    print(f"  No ISO found:                 {counts['no_iso']}")
    print(f"  Failed (no label at all):     {counts['failed']}")
    if source_counts:
        print(f"  Label sources:")
        for src, n in sorted(source_counts.items()):
            print(f"    {src:<{src_w - 2}}  {n}")
    if dry_run:
        print()
        print("Dry-run — no changes written. Re-run with --apply to write.")


def _apply_batch(session, pending: list[tuple[Disc, str]]) -> None:
    for disc, label in pending:
        disc.volume_label = label


if __name__ == "__main__":
    main()
