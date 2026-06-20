"""
Slot-aware rip job starting: gates queued RipJobs behind the global
ripping_enabled control, promotes them to running up to the configured
max_rippers concurrency limit, and spawns the actual dvdbackup work in a
background thread so the main poll loop keeps running for other
drives/region-reads/ejects.
"""

import logging
import os
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from common.models import DiscStatus, JobStatus, RipJob, Setting
from ripper_service import active_jobs
from ripper_service.job_rollback import rollback_excess_jobs
from ripper_service.rip_worker import run_dvdbackup

logger = logging.getLogger(__name__)

_MAX_RIPPERS_KEY = "max_rippers"
_DEFAULT_MAX_RIPPERS = 1
_FAKE_RIP_MODE_KEY = "fake_rip_mode"
_RIPPING_ENABLED_KEY = "ripping_enabled"
_DEFAULT_RIPPING_ENABLED = False
_JOB_START_COUNTDOWN_SECONDS = 10


def start_eligible_rip_jobs(session, cfg: dict, session_factory) -> None:
    now = datetime.utcnow()

    ripping_enabled = _get_ripping_enabled(session)
    max_rippers = _get_max_rippers(session)
    fake_rip_mode = _get_fake_rip_mode(session)

    if ripping_enabled:
        # Newly-created jobs, and ones that were paused mid-countdown,
        # become eligible to start a fresh countdown now.
        _activate_pending_countdowns(session, now)
    else:
        # Pause anything mid-countdown, and kill/roll back anything
        # already running - ripping_enabled=false means stopped, full stop.
        _pause_counting_down_jobs(session)
        rollback_excess_jobs(session, 0, cfg)

    active_count = _count_running(session)
    if active_count > max_rippers:
        rollback_excess_jobs(session, max_rippers, cfg)
        active_count = _count_running(session)

    if not ripping_enabled:
        return  # defense in depth - don't start anything while stopped

    queued_jobs = session.scalars(
        select(RipJob)
        .where(RipJob.status == JobStatus.queued)
        .where(RipJob.scheduled_start.is_not(None))
        .where(RipJob.scheduled_start <= now)
        .order_by(RipJob.scheduled_start)
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
            args=(rip_job.id, drive.device_path, scratch_dir, disc_label, fake_rip_mode, session_factory, label),
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


def _activate_pending_countdowns(session, now: datetime) -> None:
    pending_jobs = session.scalars(
        select(RipJob)
        .where(RipJob.status == JobStatus.queued)
        .where(RipJob.scheduled_start.is_(None))
    ).all()
    for rip_job in pending_jobs:
        rip_job.scheduled_start = now + timedelta(seconds=_JOB_START_COUNTDOWN_SECONDS)
    if pending_jobs:
        session.commit()


def _pause_counting_down_jobs(session) -> None:
    counting_down_jobs = session.scalars(
        select(RipJob)
        .where(RipJob.status == JobStatus.queued)
        .where(RipJob.scheduled_start.is_not(None))
    ).all()
    for rip_job in counting_down_jobs:
        rip_job.scheduled_start = None
    if counting_down_jobs:
        session.commit()


def _get_max_rippers(session) -> int:
    setting = session.get(Setting, _MAX_RIPPERS_KEY)
    return int(setting.value) if setting else _DEFAULT_MAX_RIPPERS


def _get_fake_rip_mode(session) -> bool:
    setting = session.get(Setting, _FAKE_RIP_MODE_KEY)
    return setting.value == "true" if setting else False


def _get_ripping_enabled(session) -> bool:
    setting = session.get(Setting, _RIPPING_ENABLED_KEY)
    return setting.value == "true" if setting else _DEFAULT_RIPPING_ENABLED


def _run_job(rip_job_id, device_path, scratch_dir, disc_label, fake_rip_mode, session_factory, label) -> None:
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

    session = session_factory()
    try:
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is None:
            logger.warning("RipJob %s vanished before completion handling", rip_job_id)
            return

        disc = rip_job.disc
        rip_job.completed_at = datetime.utcnow()

        if result["success"]:
            rip_job.status = JobStatus.done
            if disc is not None:
                disc.status = DiscStatus.ripped
            logger.info("Rip job %s (%s) completed successfully", rip_job_id, label)
        else:
            rip_job.status = JobStatus.error
            if disc is not None:
                disc.status = DiscStatus.error
            error_message = _extract_error_message(result.get("log") or "")
            rip_job.error_message = error_message
            logger.warning("Rip job %s (%s) failed: %s", rip_job_id, label, error_message)
            _cleanup_scratch_dir(scratch_dir)

        session.commit()
    finally:
        session.close()
        active_jobs.unregister(rip_job_id)


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
