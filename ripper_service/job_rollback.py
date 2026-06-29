"""
Shared kill/rollback mechanism for running rip jobs.

Used both when ripping_enabled is toggled off (target_active_count=0)
and when max_rippers is decreased below the current running count
(target_active_count=max_rippers) - both cases reduce to "stop the N
newest running jobs and put them back in the queue."
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import select

from common.models import DiscStatus, JobStatus, RipJob
from ripper_service import active_jobs

logger = logging.getLogger(__name__)

_TERMINATE_TIMEOUT_SECONDS = 5
_KILL_TIMEOUT_SECONDS = 5


def rollback_job(session, rip_job: RipJob, cfg: dict) -> None:
    """Roll back one specific rip job (e.g. tray opened mid-rip)."""
    _rollback_one_job(session, rip_job, cfg)


def rollback_excess_jobs(session, target_active_count: int, cfg: dict) -> None:
    """
    Kill and reset running RipJobs down to target_active_count, newest
    (most recently started) first - the newest jobs have the least
    progress to lose.
    """
    running_jobs = session.scalars(
        select(RipJob)
        .where(RipJob.status == JobStatus.running)
        .order_by(RipJob.started_at.desc())
    ).all()

    excess_count = len(running_jobs) - target_active_count
    if excess_count <= 0:
        return

    for rip_job in running_jobs[:excess_count]:
        _rollback_one_job(session, rip_job, cfg)


def _rollback_one_job(session, rip_job: RipJob, cfg: dict) -> None:
    disc = rip_job.disc
    drive = rip_job.drive
    label = (drive.label or drive.device_path) if drive else "unknown drive"

    active_jobs.mark_rolled_back(rip_job.id)
    _terminate_process(rip_job.id, label)

    if disc is not None:
        scratch_dir = str(Path(cfg["storage"]["scratch_dir"]) / str(disc.id))
        _cleanup_scratch_dir(scratch_dir)

    rip_job.status = JobStatus.queued
    rip_job.started_at = None
    rip_job.scheduled_start = None
    rip_job.progress_percent = None
    rip_job.progress_stage = None

    if disc is not None:
        disc.status = DiscStatus.queued

    session.commit()

    logger.info(
        "Rolled back rip job %s (disc %s) on %s - reset to queued",
        rip_job.id, disc.id if disc else None, label,
    )


def _terminate_process(rip_job_id: int, label: str) -> None:
    process = active_jobs.get_process(rip_job_id)
    if process is None or process.poll() is not None:
        return  # not tracked yet, or already exited on its own

    process.terminate()
    try:
        process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        logger.warning("Rip job %s on %s did not stop after terminate() - killing", rip_job_id, label)

    process.kill()
    try:
        process.wait(timeout=_KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("Rip job %s on %s did not die even after kill()", rip_job_id, label)


def _cleanup_scratch_dir(scratch_dir: str) -> None:
    try:
        if os.path.isdir(scratch_dir):
            shutil.rmtree(scratch_dir)
    except OSError:
        logger.warning("Failed to clean up scratch dir %s", scratch_dir, exc_info=True)
