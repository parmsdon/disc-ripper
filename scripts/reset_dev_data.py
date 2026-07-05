"""
Full wipe of disc-related test data, for repeated end-to-end testing
cycles without manually clearing the DB and datastore by hand.

DEV-ONLY (same hard env gate as clean_dev_test_data.py - this is far more
destructive, since it removes ALL discs/jobs/tracks/candidates, not just
identified duplicates).

Deletes every disc-related row from the DB (in FK order), all rip log
events, and all encoded/ripped files from the datastore. Resets library
and encoder status settings to their defaults.

Does NOT touch: drives, physical_drives, encode_profiles, settings
(other than the four reset below), catalog (My Movies data).

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
from common.models import (
    Catalog, CDTrack, Disc, EncodeJob, LookupCandidate,
    RipJob, RipLogEvent, Setting,
)

_SETTINGS_TO_RESET = {
    "library_last_generated": "",
    "library_last_stats": "",
    "encoder_service_status": "stopped",
    "encoder_service_heartbeat": "",
}


def _clear_dir_contents(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    for entry in directory.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    return True


def _remove_dir(directory: Path) -> bool:
    if not directory.exists():
        return False
    shutil.rmtree(directory)
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
    log_event_count = session.scalar(select(func.count()).select_from(RipLogEvent))
    catalog_count = session.scalar(select(func.count()).select_from(Catalog))

    datastore_root = Path(cfg["storage"]["datastore_root"])

    clear_dirs = [
        get_store_path(cfg, "dvd_store", "raw"),
        get_store_path(cfg, "dvd_store", "extract"),
        get_store_path(cfg, "dvd_store", "plex"),
        get_store_path(cfg, "dvd_store", "iphone"),
        get_store_path(cfg, "cd_store", "raw"),
        get_store_path(cfg, "cd_store", "flac"),
        get_store_path(cfg, "cd_store", "mp3_320"),
    ]
    library_dir = datastore_root / "library"

    print("Will delete from DB:")
    print(f"  - All discs and associated records ({disc_count} disc(s), "
          f"{rip_job_count} rip job(s), {encode_job_count} encode job(s), "
          f"{lookup_candidate_count} candidate(s), {cd_track_count} CD track(s))")
    print(f"  - All rip log events ({log_event_count})")
    print("Will clear filesystem:")
    print("  - dvd_store/raw/, extract/, plex/, iphone/")
    print("  - cd_store/raw/, flac/, mp3_320/")
    print("  - library/ (entire directory, recreated on next generation)")
    print("Will reset settings: library_last_generated, library_last_stats, "
          "encoder_service_status, encoder_service_heartbeat")
    print("Will NOT touch:")
    print("  - drives, physical_drives, settings (other than above), encode_profiles")
    print(f"  - My Movies catalog ({catalog_count} entries)")

    confirmation = input("\nType 'yes' to proceed: ").strip()
    if confirmation != "yes":
        print("Aborted - no changes made.")
        return

    # Explicit FK-ordered bulk DELETEs. The DB FK constraints are all NO
    # ACTION (not CASCADE), so children must be deleted before parents.
    session.execute(delete(CDTrack))
    session.execute(delete(EncodeJob))
    session.execute(delete(RipJob))
    session.execute(delete(LookupCandidate))
    session.execute(delete(RipLogEvent))
    session.execute(delete(Disc))

    for key, value in _SETTINGS_TO_RESET.items():
        row = session.get(Setting, key)
        if row:
            row.value = value
        # If the row doesn't exist yet, leave it absent (the app treats missing = default).

    session.commit()
    print(f"Deleted {disc_count} disc(s), {rip_job_count} rip job(s), "
          f"{encode_job_count} encode job(s), {lookup_candidate_count} candidate(s), "
          f"{cd_track_count} CD track(s), {log_event_count} log event(s).")

    for directory in clear_dirs:
        if _clear_dir_contents(directory):
            print(f"Cleared {directory}")
        else:
            print(f"Skipped (not found): {directory}")

    if _remove_dir(library_dir):
        print(f"Removed {library_dir}")
    else:
        print(f"Skipped (not found): {library_dir}")

    print("Done.")


if __name__ == "__main__":
    main(os.environ.get("DISCRIPPER_ENV", "dev"))
