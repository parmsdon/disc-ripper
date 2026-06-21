"""
Slot-aware rip job starting: gates queued RipJobs behind the global
ripping_enabled control, promotes them to running up to the configured
max_rippers concurrency limit, and spawns the actual dvdbackup/mkisofs
(DVD) or cdparanoia (CD) work in a background thread so the main poll
loop keeps running for other drives/region-reads/ejects.

RipJob.status stays "running" across both the dvdbackup and mkisofs
sub-steps (DVD) or across all tracks processed in one run (CD) - it's
Disc.status that distinguishes "ripping" (copying disc content) from
"building" (constructing the ISO, DVD only) for the UI.
"""

import logging
import os
import shutil
import threading
from pathlib import Path

from sqlalchemy import select

from common.config import get_store_path
from common.models import CDTrack, Disc, DiscStatus, DiscType, JobStatus, RipJob, RipQuality, Setting, naive_utcnow
from ripper_service import active_jobs
from ripper_service.job_rollback import rollback_excess_jobs
from ripper_service.rip_worker import run_cdparanoia, run_dvdbackup, run_mkisofs

logger = logging.getLogger(__name__)

_MAX_RIPPERS_KEY = "max_rippers"
_DEFAULT_MAX_RIPPERS = 1
_FAKE_RIP_MODE_KEY = "fake_rip_mode"
_FAKE_DIRTY_MODE_KEY = "fake_dirty_mode"
_RIPPING_ENABLED_KEY = "ripping_enabled"
_DEFAULT_RIPPING_ENABLED = False

# fake_dirty_mode only ever injects a simulated read error on this one
# drive, so testing dirty-rip detection doesn't make every fake rip dirty.
_FAKE_DIRTY_DRIVE_LABEL = "Drive 1"


def start_eligible_rip_jobs(session, cfg: dict, session_factory) -> None:
    now = naive_utcnow()

    ripping_enabled = _get_ripping_enabled(session)
    max_rippers = _get_max_rippers(session)
    fake_rip_mode = _get_fake_rip_mode(session)
    fake_dirty_mode = _get_fake_dirty_mode(session)

    if not ripping_enabled:
        # ripping_enabled=false means stopped, full stop - kill/roll back
        # anything already running.
        rollback_excess_jobs(session, 0, cfg)
        return  # defense in depth - don't start anything while stopped

    active_count = _count_running(session)
    if active_count > max_rippers:
        rollback_excess_jobs(session, max_rippers, cfg)
        active_count = _count_running(session)

    queued_jobs = session.scalars(
        select(RipJob)
        .where(RipJob.status == JobStatus.queued)
        .order_by(RipJob.created_at)
    ).all()

    for rip_job in queued_jobs:
        if active_count >= max_rippers:
            break

        disc = rip_job.disc
        drive = rip_job.drive

        if disc is None or drive is None:
            logger.warning("RipJob %s missing disc/drive - skipping", rip_job.id)
            continue

        label = drive.label or drive.device_path
        inject_dirty = fake_dirty_mode and drive.label == _FAKE_DIRTY_DRIVE_LABEL

        rip_job.status = JobStatus.running
        rip_job.started_at = now
        disc.status = DiscStatus.ripping
        session.commit()

        active_count += 1

        if disc.type == DiscType.dvd:
            scratch_dir = str(Path(cfg["storage"]["scratch_dir"]) / str(disc.id))
            disc_label = f"disc_{disc.id}"
            thread = threading.Thread(
                target=_run_job,
                args=(
                    rip_job.id, drive.device_path, scratch_dir, disc_label, fake_rip_mode,
                    session_factory, label, cfg, inject_dirty,
                ),
                daemon=True,
            )
        else:
            thread = threading.Thread(
                target=_run_cd_job,
                args=(rip_job.id, drive.device_path, fake_rip_mode, label, cfg, inject_dirty, session_factory),
                daemon=True,
            )

        active_jobs.register(rip_job.id, thread)
        thread.start()

        logger.info(
            "Started rip job %s for disc %s (%s) on %s (fake_rip_mode=%s, inject_dirty=%s)",
            rip_job.id, disc.id, disc.type.value, label, fake_rip_mode, inject_dirty,
        )


def _count_running(session) -> int:
    return len(session.scalars(select(RipJob).where(RipJob.status == JobStatus.running)).all())


def _get_max_rippers(session) -> int:
    setting = session.get(Setting, _MAX_RIPPERS_KEY)
    return int(setting.value) if setting else _DEFAULT_MAX_RIPPERS


def _get_fake_rip_mode(session) -> bool:
    setting = session.get(Setting, _FAKE_RIP_MODE_KEY)
    return setting.value == "true" if setting else False


def _get_fake_dirty_mode(session) -> bool:
    setting = session.get(Setting, _FAKE_DIRTY_MODE_KEY)
    return setting.value == "true" if setting else False


def _get_ripping_enabled(session) -> bool:
    setting = session.get(Setting, _RIPPING_ENABLED_KEY)
    return setting.value == "true" if setting else _DEFAULT_RIPPING_ENABLED


def _run_job(rip_job_id, device_path, scratch_dir, disc_label, fake_rip_mode, session_factory, label, cfg, inject_dirty) -> None:
    try:
        result = run_dvdbackup(
            device_path, scratch_dir, disc_label, fake_rip_mode, rip_job_id, session_factory,
            inject_dirty=inject_dirty,
        )
    except Exception:
        logger.exception("Unexpected error running rip job %s", rip_job_id)
        result = {"success": False, "log": "Unexpected error - see service logs", "return_code": None}

    if active_jobs.was_rolled_back(rip_job_id):
        # job_rollback.py already set the authoritative final state
        # (queued) and cleaned up - don't overwrite it with error/done.
        logger.info("Rip job %s was rolled back externally - skipping normal completion handling", rip_job_id)
        active_jobs.unregister(rip_job_id)
        return

    if not result["success"]:
        _fail_job(rip_job_id, label, result, scratch_dir, session_factory)
        active_jobs.unregister(rip_job_id)
        return

    # dvdbackup succeeded (possibly "dirty" - padded over a recoverable
    # read error) - move on to building the ISO regardless; dirty is just
    # recorded for now and checked again at final completion below. Disc
    # shows "building" (distinct from "ripping") while this runs.
    dirty = result.get("dirty", False)
    disc_id = _mark_building(rip_job_id, label, session_factory)
    if disc_id is None:
        active_jobs.unregister(rip_job_id)
        return

    video_ts_parent = str(Path(scratch_dir) / disc_label)
    iso_dir = get_store_path(cfg, "dvd_store", "raw", str(disc_id))
    iso_path = str(iso_dir / f"{disc_label}.iso")

    try:
        mkiso_result = run_mkisofs(video_ts_parent, iso_path, fake_rip_mode, rip_job_id, session_factory)
    except Exception:
        logger.exception("Unexpected error running mkisofs for rip job %s", rip_job_id)
        mkiso_result = {"success": False, "log": "Unexpected error - see service logs", "return_code": None}

    if active_jobs.was_rolled_back(rip_job_id):
        logger.info(
            "Rip job %s was rolled back externally during mkisofs - skipping normal completion handling",
            rip_job_id,
        )
        active_jobs.unregister(rip_job_id)
        return

    if mkiso_result["success"]:
        _finish_success(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty)
    else:
        _fail_job(rip_job_id, label, mkiso_result, scratch_dir, session_factory)

    active_jobs.unregister(rip_job_id)


def _mark_building(rip_job_id, label, session_factory):
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before mkisofs step", rip_job_id)
            return None
        disc = rip_job.disc
        if disc is None:
            logger.warning("RipJob %s has no disc - skipping mkisofs step", rip_job_id)
            return None
        disc.status = DiscStatus.building
        session.commit()
        logger.info("Rip job %s (%s) entering building phase", rip_job_id, label)
        return disc.id
    finally:
        session.close()


def _finish_success(rip_job_id, label, disc_id, iso_dir, cfg, scratch_dir, session_factory, dirty: bool) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        disc = session.get(Disc, disc_id)
        if rip_job is None or disc is None:
            logger.warning("RipJob %s or disc %s vanished before final completion", rip_job_id, disc_id)
            return

        relative_path = str(iso_dir.relative_to(Path(cfg["storage"]["datastore_root"])))

        disc.raw_path = relative_path
        disc.ripped_at = naive_utcnow()
        if disc.drive is not None and disc.drive.physical_drive is not None:
            disc.ripped_in_region = disc.drive.physical_drive.region

        # dirty -> flag for a re-rip next time this disc is reinserted
        # (main.py's detection dedup checks needs_rerip). clean -> clear
        # any prior dirty flag, e.g. this is a re-rip that's now succeeded.
        disc.rip_quality = "dirty" if dirty else "clean"
        disc.needs_rerip = dirty

        # A working title set before the rip finished (e.g. typed in while
        # ripping/building) goes straight to "ripped" - otherwise there's
        # nothing to identify it by yet, so it needs a stop in
        # "identifying" until temp-name is set via the API.
        disc.status = DiscStatus.ripped if disc.temp_name else DiscStatus.identifying

        rip_job.status = JobStatus.done
        rip_job.completed_at = naive_utcnow()

        session.commit()
        logger.info(
            "Rip job %s (%s) completed successfully - ISO at %s (rip_quality=%s)",
            rip_job_id, label, relative_path, disc.rip_quality,
        )
    finally:
        session.close()
        _cleanup_scratch_dir(scratch_dir)


def _fail_job(rip_job_id, label, result, scratch_dir, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before error handling", rip_job_id)
            return
        disc = rip_job.disc

        rip_job.status = JobStatus.error
        rip_job.completed_at = naive_utcnow()
        error_message = _extract_error_message(result.get("log") or "")
        rip_job.error_message = error_message
        if disc is not None:
            disc.status = DiscStatus.error

        session.commit()
        logger.warning("Rip job %s (%s) failed: %s", rip_job_id, label, error_message)
    finally:
        session.close()
        _cleanup_scratch_dir(scratch_dir)


# ---------------------------------------------------------------------------
# CD: one job processes all tracks needing a rip, sequentially (one drive
# can only read one track at a time anyway) - no separate build step.
# ---------------------------------------------------------------------------

_NEEDS_RIP_TRACK_QUALITIES = (None, RipQuality.imperfect, RipQuality.failed)


def _run_cd_job(rip_job_id, device_path, fake_rip_mode, label, cfg, inject_dirty, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before CD job started", rip_job_id)
            return
        disc = rip_job.disc
        if disc is None:
            logger.warning("RipJob %s has no disc - aborting CD job", rip_job_id)
            return
        disc_id = disc.id

        # Fresh disc: every track has rip_quality=None, so all of them
        # need ripping. Re-rip of a dirty disc: only the imperfect/failed
        # ones do - already-"good" tracks are left untouched.
        pending = sorted(
            (t for t in disc.tracks if t.rip_quality in _NEEDS_RIP_TRACK_QUALITIES),
            key=lambda t: t.track_number,
        )
        track_numbers = [t.track_number for t in pending]
        track_durations = {t.track_number: t.duration_seconds for t in pending}

        cd_dir = get_store_path(cfg, "cd_store", "raw", str(disc_id))
        disc.raw_path = str(cd_dir.relative_to(Path(cfg["storage"]["datastore_root"])))
        session.commit()
    finally:
        session.close()

    total = len(track_numbers)
    if total == 0:
        logger.warning("RipJob %s (disc %s) has no tracks needing a rip - nothing to do", rip_job_id, disc_id)
        _finish_cd_job(rip_job_id, label, disc_id, session_factory)
        active_jobs.unregister(rip_job_id)
        return

    for index, track_number in enumerate(track_numbers, start=1):
        output_path = str(cd_dir / f"track{track_number:02d}.wav")
        stage_label = f"Track {index}/{total}"
        # fake_dirty_mode simulates one bad track on a disc, not a wholly
        # unreadable one - only the first track processed gets it.
        inject_dirty_this_track = inject_dirty and index == 1

        try:
            result = run_cdparanoia(
                device_path, track_number, output_path, track_durations.get(track_number), stage_label,
                fake_rip_mode, rip_job_id, session_factory, inject_dirty=inject_dirty_this_track,
            )
        except Exception:
            logger.exception("Unexpected error ripping track %s for rip job %s", track_number, rip_job_id)
            result = {"success": False, "log": "Unexpected error - see service logs", "return_code": None}

        if active_jobs.was_rolled_back(rip_job_id):
            logger.info(
                "Rip job %s was rolled back externally during track %s - skipping normal completion handling",
                rip_job_id, track_number,
            )
            active_jobs.unregister(rip_job_id)
            return

        # A single bad/scratched track is normal and shouldn't stop the
        # rest of the disc - only return_code=None (process never even
        # ran: rolled back, or couldn't launch) suggests a deeper
        # drive/device problem worth aborting the whole disc for.
        hard_failure = not result["success"] and result.get("return_code") is None
        _record_track_result(disc_id, track_number, result, label, session_factory)

        if hard_failure:
            _fail_cd_job(rip_job_id, label, disc_id, result, session_factory)
            active_jobs.unregister(rip_job_id)
            return

    _finish_cd_job(rip_job_id, label, disc_id, session_factory)
    active_jobs.unregister(rip_job_id)


def _record_track_result(disc_id, track_number, result, label, session_factory) -> None:
    session = session_factory()
    try:
        track = session.scalars(
            select(CDTrack).where(CDTrack.disc_id == disc_id, CDTrack.track_number == track_number)
        ).first()
        if track is None:
            logger.warning("CDTrack %s for disc %s vanished - skipping result recording", track_number, disc_id)
            return

        track.rip_log = result.get("log")
        if result["success"]:
            track.rip_quality = RipQuality.imperfect if result.get("dirty") else RipQuality.good
            track.wav_filename = f"track{track_number:02d}.wav"
        else:
            track.rip_quality = RipQuality.failed

        session.commit()
        logger.info(
            "Track %s for disc %s (%s) -> %s", track_number, disc_id, label, track.rip_quality.value,
        )
    finally:
        session.close()


def _finish_cd_job(rip_job_id, label, disc_id, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        disc = session.get(Disc, disc_id)
        if rip_job is None or disc is None:
            logger.warning("RipJob %s or disc %s vanished before final CD completion", rip_job_id, disc_id)
            return

        # "Clean" only if every track, across all attempts (not just the
        # ones processed in this run), is currently "good".
        any_bad = any(t.rip_quality in (RipQuality.imperfect, RipQuality.failed) for t in disc.tracks)
        disc.rip_quality = "dirty" if any_bad else "clean"
        disc.needs_rerip = any_bad
        disc.ripped_at = naive_utcnow()
        disc.status = DiscStatus.ripped if disc.temp_name else DiscStatus.identifying

        rip_job.status = JobStatus.done
        rip_job.completed_at = naive_utcnow()

        session.commit()
        logger.info(
            "CD rip job %s (%s) completed - disc #%s rip_quality=%s",
            rip_job_id, label, disc_id, disc.rip_quality,
        )
    finally:
        session.close()


def _fail_cd_job(rip_job_id, label, disc_id, result, session_factory) -> None:
    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before CD error handling", rip_job_id)
            return
        disc = rip_job.disc

        rip_job.status = JobStatus.error
        rip_job.completed_at = naive_utcnow()
        error_message = _extract_error_message(result.get("log") or "")
        rip_job.error_message = error_message
        if disc is not None:
            disc.status = DiscStatus.error

        session.commit()
        logger.warning(
            "CD rip job %s (%s) aborted - drive/device problem, not just a bad track: %s",
            rip_job_id, label, error_message,
        )
    finally:
        session.close()


def _extract_error_message(log_text: str) -> str:
    for line in log_text.splitlines():
        if "error" in line.lower():
            return line.strip()
    return log_text[-500:].strip()


def _cleanup_scratch_dir(scratch_dir: str) -> None:
    try:
        if os.path.isdir(scratch_dir):
            shutil.rmtree(scratch_dir)
    except OSError:
        logger.warning("Failed to clean up scratch dir %s", scratch_dir, exc_info=True)
