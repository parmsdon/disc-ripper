"""
Bulk ISO import CLI.

Usage:
    DISCRIPPER_ENV=dev python3 scripts/bulk_import.py [--dry-run]

Imports all ISOs from dvd_store/legacy/ that have not already been imported.
Prints one line per file, then a summary.
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
from common.import_helpers import (
    build_catalog_lookup,
    import_one_iso,
)
from common.models import Disc, DiscStatus


def _preflight_check(datastore_root: str) -> str | None:
    errors = []
    for rel in ["dvd_store/legacy", "dvd_store/raw"]:
        d = Path(datastore_root) / rel
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(f"{rel}: cannot create ({exc.strerror})")
            continue
        if not os.access(str(d), os.W_OK):
            errors.append(f"{rel}: not writable")
    return "; ".join(errors) if errors else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk ISO import from dvd_store/legacy/")
    parser.add_argument("--dry-run", action="store_true", help="Simulate import without DB writes")
    args = parser.parse_args()

    env = os.environ.get("DISCRIPPER_ENV", "dev")
    cfg = load_config(env)
    db_url = get_db_url(cfg)
    datastore_root = cfg["storage"]["datastore_root"]

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    preflight_err = _preflight_check(datastore_root)
    if preflight_err:
        print(f"Error: {preflight_err}", file=sys.stderr)
        sys.exit(1)

    legacy_dir = Path(datastore_root) / "dvd_store" / "legacy"

    legacy_isos = sorted(legacy_dir.glob("*.iso"), key=lambda p: p.name.lower())
    legacy_filenames = {p.name for p in legacy_isos}

    with Session() as session:
        error_rows = session.execute(
            select(Disc.imported_filename, Disc.status)
            .where(
                Disc.imported == True,
                Disc.status == DiscStatus.error,
                Disc.imported_filename.isnot(None),
            )
        ).all()
    extra_filenames = [row[0] for row in error_rows if row[0] not in legacy_filenames]
    all_filenames = [p.name for p in legacy_isos] + extra_filenames

    if not all_filenames:
        print("No ISO files found in legacy/.")
        return

    with Session() as session:
        catalog_lookup = build_catalog_lookup(session)

    imported = skipped = already_imported = 0

    for filename in all_filenames:
        with Session() as session:
            result = import_one_iso(
                session, datastore_root, filename,
                dry_run=args.dry_run,
                catalog_lookup=catalog_lookup,
            )

        status = result["status"]
        wt = result["working_title"] or "(unknown)"
        match_str = ""
        if result["catalog_match"] == "exact" and result["catalog_title"]:
            match_str = f'[exact: "{result["catalog_title"][:40]}"]'
        elif result["catalog_match"] == "ambiguous":
            match_str = "[ambiguous match]"
        elif result["reason"]:
            match_str = result["reason"]

        if status == "imported":
            tag = "imported "
            imported += 1
        elif status == "already_imported":
            tag = "already  "
            already_imported += 1
        else:
            tag = "skipped  "
            skipped += 1

        print(f"{tag}  {wt:<55} {match_str}")

    total = len(all_filenames)
    dry_note = " (dry run)" if args.dry_run else ""
    print(f"\nDone{dry_note}: {imported} imported, {already_imported} already imported, {skipped} skipped (of {total} total)")


if __name__ == "__main__":
    main()
