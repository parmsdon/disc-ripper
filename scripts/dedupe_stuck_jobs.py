"""
One-off cleanup for duplicate Disc/RipJob rows created by a since-fixed
ripper_service bug: every service restart lost its in-memory
media_present_by_device tracking, so a disc that was never physically
removed looked like a fresh insert on the next poll, creating a brand
new Disc + RipJob for it. Fixed in ripper_service/main.py with a
disc_fingerprint + drive_id dedup check before creating a new Disc.

This script is meant to be run ONCE BY HAND against a database that
already has leftover duplicates from before that fix (e.g. the dev DB).
It is not part of normal operation and nothing imports or schedules it.

For each (drive_id, disc_fingerprint) group of status="queued" discs
with more than one row, it keeps the oldest (lowest id) disc and
deletes the rest, along with their rip_jobs (cascade). Discs with a
null disc_fingerprint are skipped - there's no way to safely tell
whether two such discs are duplicates or genuinely different.

Usage:
    DISCRIPPER_ENV=dev python3 scripts/dedupe_stuck_jobs.py
"""

import os
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from common.config import load_config, get_db_url
from common.models import Disc, DiscStatus


def find_duplicate_groups(session):
    discs = session.scalars(
        select(Disc).where(Disc.status == DiscStatus.queued).order_by(Disc.id)
    ).all()

    groups = defaultdict(list)
    for disc in discs:
        if disc.disc_fingerprint is None:
            continue
        groups[(disc.drive_id, disc.disc_fingerprint)].append(disc)

    return {key: discs for key, discs in groups.items() if len(discs) > 1}


def main(env: str) -> None:
    cfg = load_config(env)
    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()

    duplicate_groups = find_duplicate_groups(session)

    if not duplicate_groups:
        print("No duplicate queued discs found - nothing to do.")
        return

    to_delete = []
    print("Duplicate groups found:\n")
    for (drive_id, fingerprint), discs in duplicate_groups.items():
        discs_sorted = sorted(discs, key=lambda d: d.id)
        keeper, dupes = discs_sorted[0], discs_sorted[1:]

        print(f"  drive_id={drive_id} fingerprint={fingerprint!r}")
        keeper_job_ids = [job.id for job in keeper.rip_jobs]
        print(f"    keep:   disc #{keeper.id} (created {keeper.created_at}), rip_job(s) {keeper_job_ids}")
        for dupe in dupes:
            dupe_job_ids = [job.id for job in dupe.rip_jobs]
            print(f"    delete: disc #{dupe.id} (created {dupe.created_at}), rip_job(s) {dupe_job_ids}")
        print()

        to_delete.extend(dupes)

    print(f"{len(to_delete)} duplicate disc(s) (and their rip_jobs) will be permanently deleted.")
    confirmation = input("Type 'yes' to proceed: ").strip()
    if confirmation != "yes":
        print("Aborted - no changes made.")
        return

    for disc in to_delete:
        session.delete(disc)
    session.commit()
    print(f"Deleted {len(to_delete)} duplicate disc(s).")


if __name__ == "__main__":
    main(os.environ.get("DISCRIPPER_ENV", "dev"))
