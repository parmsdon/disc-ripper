"""
Full wipe of disc-related test data, for repeated end-to-end testing
cycles without manually clearing the DB and datastore by hand.

DEV-ONLY (same hard env gate as clean_dev_test_data.py - this is far more
destructive, since it removes ALL discs/jobs/tracks/candidates, not just
identified duplicates).

Explicitly deletes every row from cd_tracks, encode_jobs, rip_jobs, and
lookup_candidates first (in FK order, since the DB FK constraints are NO
ACTION not CASCADE), then discs. Also
removes all files/directories under dvd_store/raw/ and cd_store/raw/, and
under each encode profile's output_subfolder in both stores, on the
configured datastore_root.

Does NOT touch drives, physical_drives, encode_profiles, or settings -
those are configuration, not test data.

Usage:
    DISCRIPPER_ENV=dev python3 scripts/reset_dev_data.py
"""

import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import sessionmaker

from common.config import get_db_url, get_store_path, load_config
from common.models import CDTrack, Disc, EncodeJob, EncodeProfile, LookupCandidate, RipJob

_STORES = ("dvd_store", "cd_store")


def _clear_dir_contents(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    for entry in directory.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    return True


def main(env: str) -> None:
    if env != "dev":
        print(f"Refusing to run against env={env!r} - this script is dev-only.")
        sys.exit(1)

    cfg = load_config(env)
    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()

    disc_count = session.scalar(select(func.count()).select_from(Disc))
    rip_job_count = session.scalar(select(func.count()).select_from(RipJob))
    encode_job_count = session.scalar(select(func.count()).select_from(EncodeJob))
    lookup_candidate_count = session.scalar(select(func.count()).select_from(LookupCandidate))
    cd_track_count = session.scalar(select(func.count()).select_from(CDTrack))

    if not disc_count:
        print("No discs found - nothing to do.")
        return

    raw_dirs = [get_store_path(cfg, store, "raw") for store in _STORES]
    profile_subfolders = session.scalars(select(EncodeProfile.output_subfolder)).all()
    profile_dirs = [
        get_store_path(cfg, store, subfolder)
        for store in _STORES
        for subfolder in profile_subfolders
    ]

    print("This will permanently delete:")
    print(f"  {disc_count} disc(s)")
    print(f"  {rip_job_count} rip_job(s)")
    print(f"  {encode_job_count} encode_job(s)")
    print(f"  {lookup_candidate_count} lookup_candidate(s)")
    print(f"  {cd_track_count} cd_track(s)")
    print("  all files under:")
    for directory in raw_dirs + profile_dirs:
        print(f"    {directory}")
    print("  identification data (catalog matches, album/track metadata) will be reset")
    print("\ndrives, physical_drives, encode_profiles, and settings will NOT be touched.")

    confirmation = input("\nType 'yes' to proceed: ").strip()
    if confirmation != "yes":
        print("Aborted - no changes made.")
        return

    # Explicit FK-ordered bulk DELETEs rather than ORM cascade. The DB FK
    # constraints are all NO ACTION (not CASCADE), so children must be
    # deleted before parents or Postgres will raise a FK violation. ORM
    # cascade via session.delete() lazy-loads each disc's relationships
    # individually before issuing DELETEs, which is slower and can order
    # incorrectly if any lazy load races with the live ripper service's
    # poll transaction. Bulk DELETE in the right order is simpler and safe.
    session.execute(delete(CDTrack))
    session.execute(delete(EncodeJob))
    session.execute(delete(RipJob))
    session.execute(delete(LookupCandidate))
    session.execute(delete(Disc))
    # The UPDATEs below are no-ops when all discs/tracks/candidates were
    # deleted above, but are kept as explicit steps for a future --keep-discs
    # mode so the identification queue is fully repopulated on next rip.
    session.execute(update(Disc).values(mb_disc_id=None, mb_toc=None, mb_lookup_status=None))
    session.execute(update(Disc).values(catalog_id=None))
    session.execute(update(Disc).values(album_title=None, album_artist=None))
    session.execute(update(CDTrack).values(title=None, artist=None))
    session.execute(update(LookupCandidate).values(selected=False))
    session.commit()
    print(f"Deleted {disc_count} disc(s), {rip_job_count} rip_job(s), "
          f"{encode_job_count} encode_job(s), {lookup_candidate_count} "
          f"lookup_candidate(s), {cd_track_count} cd_track(s).")

    for directory in raw_dirs + profile_dirs:
        if _clear_dir_contents(directory):
            print(f"Cleared {directory}")

    print("Done.")


if __name__ == "__main__":
    main(os.environ.get("DISCRIPPER_ENV", "dev"))
