"""
Slot-aware rip job starting: gates queued RipJobs behind the global
ripping_enabled control, promotes them to running up to the configured
max_rippers concurrency limit, and spawns the actual dvdbackup/mkisofs
work in a background thread so the main poll loop keeps running for
other drives/region-reads/ejects.

RipJob.status stays "running" across both the dvdbackup and mkisofs
sub-steps - it's Disc.status that distinguishes "ripping" (copying disc
content) from "building" (constructing the ISO) for the UI.
"""

import logging
import os
import shutil
import threading
from pathlib import Path

from sqlalchemy import select

from common.config import get_store_path
from common.models import Disc, DiscStatus, JobStatus, RipJob, Setting, naive_utcnow
from ripper_service import active_jobs
from ripper_service.job_rollback import rollback_excess_jobs
from ripper_service.rip_worker import run_dvdbackup, run_mkisofs

logger = logging.getLogger(__name__)

_MAX_RIPPERS_KEY = "max_rippers"
_DEFAULT_MAX_RIPPERS = 1
_FAKE_RIP_MODE_KEY = "fake_rip_mode"
_RIPPING_ENABLED_KEY = "ripping_enabled"
_DEFAULT_RIPPING_ENABLED = False


def start_eligible_rip_jobs(session, cfg: dict, session_factory) -> None:
    now = naive_utcnow()

    ripping_enabled = _get_ripping_enabled(session)
    max_rippers = _get_max_rippers(session)
    fake_rip_mode = _get_fake_rip_mode(session)

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

        rip_job.status = JobStatus.running
        rip_job.started_at = now
        disc.status = DiscStatus.ripping
        session.commit()

        active_count += 1

        scratch_dir = str(Path(cfg["storage"]["scratch_dir"]) / str(disc.id))
        disc_label = f"disc_{disc.id}"

        thread = threading.Thread(
            target=_run_job,
            args=(rip_job.id, drive.device_path, scratch_dir, disc_label, fake_rip_mode, session_factory, label, cfg),
            daemon=True,
        )
        active_jobs.register(rip_job.id, thread)
        thread.start()

        logger.info(
            "Started rip job %s for disc %s on %s (fake_rip_mode=%s, scratch_dir=%s)",
            rip_job.id, disc.id, label, fake_rip_mode, scratch_dir,
        )


def _count_running(session) -> int:
    return len(session.scalars(select(RipJob).where(RipJob.status == JobStatus.running)).all())


def _get_max_rippers(session) -> int:
    setting = session.get(Setting, _MAX_RIPPERS_KEY)
    return int(setting.value) if setting else _DEFAULT_MAX_RIPPERS


def _get_fake_rip_mode(session) -> bool:
    setting = session.get(Setting, _FAKE_RIP_MODE_KEY)
    return setting.value == "true" if setting else False


def _get_ripping_enabled(session) -> bool:
    setting = session.get(Setting, _RIPPING_ENABLED_KEY)
    return setting.value == "true" if setting else _DEFAULT_RIPPING_ENABLED


def _run_job(rip_job_id, device_path, scratch_dir, disc_label, fake_rip_mode, session_factory, label, cfg) -> None:
    try:
        result = run_dvdbackup(device_path, scratch_dir, disc_label, fake_rip_mode, rip_job_id, session_factory)
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
