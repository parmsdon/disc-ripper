"""
Invalidate encode jobs whose source was replaced by a re-rip.

Called from job_starter._finish_success (DVD) and _finish_cd_job (CD)
immediately before create_encode_jobs queues fresh work. Encode jobs
that are still running are set to error so the guard in
encode_dvd_title / encode_cd_track prevents them from being overwritten
with 'done' when their subprocess finishes; their output files are left
in place until the fresh job overwrites them. Jobs that are queued or
done have their output files deleted before being marked error.
"""

import logging
import os
from pathlib import Path

from sqlalchemy import select

from common.models import EncodeJob, JobStatus

logger = logging.getLogger(__name__)


def invalidate_encode_jobs_for_rerip(
    session,
    disc_id: int,
    datastore_root: str,
    track_ids: list[int] | None = None,
) -> tuple[int, int]:
    """
    Mark stale encode jobs as error and delete their output files.

    For DVD pass track_ids=None; all encode jobs for the disc are invalidated.
    For CD pass the CDTrack.id values whose WAVs were replaced this run.

    Returns (n_invalidated, n_were_running) for the caller's log message.
    """
    q = select(EncodeJob).where(
        EncodeJob.disc_id == disc_id,
        EncodeJob.status != JobStatus.error,
    )
    if track_ids is not None:
        if not track_ids:
            return 0, 0
        q = q.where(EncodeJob.track_id.in_(track_ids))

    jobs = session.scalars(q).all()
    if not jobs:
        return 0, 0

    n_were_running = 0

    for job in jobs:
        is_running = job.status == JobStatus.running
        if is_running:
            n_were_running += 1

        if not is_running and job.output_path:
            _delete_output(job.output_path, job.id)

        job.status = JobStatus.error
        job.error_message = "Source replaced by re-rip"
        job.started_at = None
        job.completed_at = None
        job.progress_percent = None
        job.progress_stage = None

    session.commit()
    return len(jobs), n_were_running


def _delete_output(output_path: str, job_id: int) -> None:
    """Delete the output file and its directory if then empty."""
    try:
        os.remove(output_path)
    except FileNotFoundError:
        return
    except Exception:
        logger.warning("Could not delete encode output %s (job %s)", output_path, job_id, exc_info=True)
        return

    parent = Path(output_path).parent
    try:
        parent.rmdir()
    except OSError:
        pass
