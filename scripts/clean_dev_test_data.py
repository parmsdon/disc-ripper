"""
Dev-only: remove fake/test disc data created during development testing.

Hard-blocked if DISCRIPPER_ENV != 'dev'.

Actions (in order):
  1. Delete DVD discs whose raw_path points to a missing or undersized ISO.
  2. Reset failed DVD Extract encode jobs (non-zero exit) to queued,
     where the disc's ISO is genuinely valid.
  3. Delete CD discs where no track has a valid (>= MIN_WAV_SIZE) WAV file.

Real ISOs and WAV files are never touched.

Usage:
    DISCRIPPER_ENV=dev python3 scripts/clean_dev_test_data.py
"""

import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

_env = os.environ.get("DISCRIPPER_ENV", "dev")
if _env != "dev":
    print(f"ERROR: DISCRIPPER_ENV='{_env}'. This script is dev-only.", file=sys.stderr)
    sys.exit(1)

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import load_config, get_db_url
from common.models import Disc, DiscType, EncodeJob, EncodeProfile, JobStatus

MIN_ISO_SIZE = 100 * 1024 * 1024  # 100 MB
MIN_WAV_SIZE = 1 * 1024 * 1024    # 1 MB


def _iso_is_valid(disc_dir: Path) -> bool:
    """True if disc_dir contains a *.iso file >= MIN_ISO_SIZE."""
    if not disc_dir.exists():
        return False
    for iso in disc_dir.glob("*.iso"):
        try:
            if iso.stat().st_size >= MIN_ISO_SIZE:
                return True
        except OSError:
            pass
    return False


def _wav_is_valid(wav_path: Path) -> bool:
    """True if the WAV file exists and is >= MIN_WAV_SIZE."""
    try:
        return wav_path.exists() and wav_path.stat().st_size >= MIN_WAV_SIZE
    except OSError:
        return False


def _reset_job(job: EncodeJob) -> None:
    job.status = JobStatus.queued
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    job.progress_percent = None
    job.progress_stage = None
    job.log = None


def _rmdir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
        print(f"    deleted {path}")


def main() -> None:
    cfg = load_config(_env)
    datastore_root = Path(cfg["storage"]["datastore_root"])

    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)

    with Session() as session:
        # ----------------------------------------------------------------
        # Collect: fake DVD discs (raw_path set but ISO missing/undersized)
        # ----------------------------------------------------------------
        dvd_discs = session.scalars(
            select(Disc).where(Disc.type == DiscType.dvd, Disc.raw_path.isnot(None))
        ).all()

        fake_dvd_discs = [
            d for d in dvd_discs
            if not _iso_is_valid(datastore_root / d.raw_path)
        ]

        # ----------------------------------------------------------------
        # Collect: failed DVD Extract jobs on discs with a valid ISO
        # ----------------------------------------------------------------
        extract_profile = session.scalar(
            select(EncodeProfile).where(EncodeProfile.name == "DVD Extract")
        )

        failed_extract_jobs: list[EncodeJob] = []
        if extract_profile:
            candidates = session.scalars(
                select(EncodeJob).where(
                    EncodeJob.profile_id == extract_profile.id,
                    EncodeJob.status == JobStatus.error,
                    EncodeJob.error_message.ilike("%non-zero%"),
                )
            ).all()
            for job in candidates:
                disc = job.disc
                if disc and disc.raw_path and _iso_is_valid(datastore_root / disc.raw_path):
                    failed_extract_jobs.append(job)

        # ----------------------------------------------------------------
        # Collect: fake CD discs (no track has a valid WAV file)
        # ----------------------------------------------------------------
        cd_discs = session.scalars(select(Disc).where(Disc.type == DiscType.cd)).all()

        # CD encode profile output folders for filesystem cleanup
        cd_profiles = session.scalars(
            select(EncodeProfile).where(EncodeProfile.media_type == "cd")
        ).all()
        cd_output_folders = [p.output_folder for p in cd_profiles]

        fake_cd_discs = []
        for disc in cd_discs:
            if not disc.tracks:
                fake_cd_discs.append(disc)
                continue
            has_valid = any(
                track.wav_filename and disc.raw_path
                and _wav_is_valid(datastore_root / disc.raw_path / track.wav_filename)
                for track in disc.tracks
            )
            if not has_valid:
                fake_cd_discs.append(disc)

        # ----------------------------------------------------------------
        # Summary and confirmation
        # ----------------------------------------------------------------
        print()
        print(f"Will delete:          {len(fake_dvd_discs)} fake DVD disc(s), "
              f"{len(fake_cd_discs)} fake CD disc(s)")
        print(f"Will reset to queued: {len(failed_extract_jobs)} cancelled DVD encode job(s)")
        print("Real ISOs and WAV files will NOT be touched")
        print()

        if not fake_dvd_discs and not failed_extract_jobs and not fake_cd_discs:
            print("Nothing to do.")
            return

        answer = input('Type "yes" to proceed: ').strip()
        if answer != "yes":
            print("Aborted.")
            return

        print()

        # ----------------------------------------------------------------
        # Action 1: delete fake DVD discs
        # ----------------------------------------------------------------
        deleted_dvd = 0
        for disc in fake_dvd_discs:
            print(f"  DVD disc #{disc.id} ({disc.temp_name or 'no title'}):")
            _rmdir(datastore_root / disc.raw_path)
            session.delete(disc)
            deleted_dvd += 1

        if deleted_dvd:
            session.flush()
        print(f"Deleted {deleted_dvd} fake DVD disc record(s).")

        # ----------------------------------------------------------------
        # Action 2: reset failed DVD Extract jobs (and error dependents)
        # ----------------------------------------------------------------
        reset_jobs = 0
        for extract_job in failed_extract_jobs:
            disc = extract_job.disc
            print(f"  Resetting Extract job #{extract_job.id} "
                  f"(disc #{disc.id if disc else '?'}):")
            _reset_job(extract_job)
            print(f"    reset encode_job #{extract_job.id} (DVD Extract)")
            reset_jobs += 1

            if disc and extract_profile:
                for dep_job in disc.encode_jobs:
                    if (
                        dep_job.id != extract_job.id
                        and dep_job.status == JobStatus.error
                        and dep_job.profile
                        and dep_job.profile.depends_on_profile_id == extract_profile.id
                    ):
                        _reset_job(dep_job)
                        print(f"    reset encode_job #{dep_job.id} ({dep_job.profile.name})")

        print(f"Reset {reset_jobs} cancelled DVD encode job(s) to queued.")

        # ----------------------------------------------------------------
        # Action 3: delete fake CD discs
        # ----------------------------------------------------------------
        deleted_cd = 0
        for disc in fake_cd_discs:
            print(f"  CD disc #{disc.id} ({disc.album_title or disc.temp_name or 'no title'}):")
            if disc.raw_path:
                _rmdir(datastore_root / disc.raw_path)
            for folder in cd_output_folders:
                _rmdir(datastore_root / folder / str(disc.id))
            session.delete(disc)
            deleted_cd += 1

        if deleted_cd:
            session.flush()
        print(f"Deleted {deleted_cd} fake CD disc record(s).")

        session.commit()
        print("\nDone.")


if __name__ == "__main__":
    main()
