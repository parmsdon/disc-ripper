"""
One-off DEV-ONLY cleanup for leftover fake_rip_mode test duplicates.

While verifying the ripper_service scheduling fix, ~18 duplicate queued
Discs (created by the now-fixed restart-dedup bug, see
scripts/dedupe_stuck_jobs.py) were driven through to completion with
fake_rip_mode on, turning them into duplicate status="ripped" Discs
with fake ISO files under dvd_store/raw/<id>/ instead. None of that is
real rip output - it's test noise from interactive debugging, not
production data.

This is NOT the general dedup tool (that's dedupe_stuck_jobs.py, which
stays scoped to status="queued" only and is safe to run anywhere). This
script targets completed/failed test duplicates specifically and is
hard-blocked outside dev, since deleting "ripped"/"done"/"error" discs
would destroy genuine completed rips in a real environment.

For each (drive_id, disc_fingerprint) group of status IN ('ripped',
'done', 'error') discs with more than one row, keeps the oldest (lowest
id) and deletes the rest - their rip_jobs (cascade) and their raw_path
directory on disk, if any.

Usage:
    DISCRIPPER_ENV=dev python3 scripts/clean_dev_test_data.py
"""

import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import load_config, get_db_url
from common.models import Disc, DiscStatus

_COMPLETED_STATUSES = (DiscStatus.ripped, DiscStatus.done, DiscStatus.error)


def find_duplicate_groups(session):
    discs = session.scalars(
        select(Disc).where(Disc.status.in_(_COMPLETED_STATUSES)).order_by(Disc.id)
    ).all()

    groups = defaultdict(list)
    for disc in discs:
        if disc.disc_fingerprint is None:
            continue
        groups[(disc.drive_id, disc.disc_fingerprint)].append(disc)

    return {key: discs for key, discs in groups.items() if len(discs) > 1}


def main(env: str) -> None:
    if env != "dev":
        print(f"Refusing to run against env={env!r} - this script is dev-only.")
        sys.exit(1)

    cfg = load_config(env)
    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()

    duplicate_groups = find_duplicate_groups(session)

    if not duplicate_groups:
        print("No duplicate completed/failed discs found - nothing to do.")
        return

    to_delete = []
    print("Duplicate groups found:\n")
    for (drive_id, fingerprint), discs in duplicate_groups.items():
        discs_sorted = sorted(discs, key=lambda d: d.id)
        keeper, dupes = discs_sorted[0], discs_sorted[1:]

        print(f"  drive_id={drive_id} fingerprint={fingerprint!r}")
        print(f"    keep:   disc #{keeper.id} (status={keeper.status.value}, created {keeper.created_at})")
        for dupe in dupes:
            dupe_job_ids = [job.id for job in dupe.rip_jobs]
            print(
                f"    delete: disc #{dupe.id} (status={dupe.status.value}, created {dupe.created_at}), "
                f"rip_job(s) {dupe_job_ids}, raw_path={dupe.raw_path!r}"
            )
        print()

        to_delete.extend(dupes)

    print(f"{len(to_delete)} duplicate disc(s), their rip_jobs, and any raw_path files will be permanently deleted.")
    confirmation = input("Type 'yes' to proceed: ").strip()
    if confirmation != "yes":
        print("Aborted - no changes made.")
        return

    datastore_root = Path(cfg["storage"]["datastore_root"])
    for disc in to_delete:
        if disc.raw_path:
            disc_dir = datastore_root / disc.raw_path
            if disc_dir.is_dir():
                shutil.rmtree(disc_dir)
                print(f"Removed {disc_dir}")
        session.delete(disc)

    session.commit()
    print(f"Deleted {len(to_delete)} duplicate disc(s).")


if __name__ == "__main__":
    main(os.environ.get("DISCRIPPER_ENV", "dev"))
