"""
EncodeJob rollback: reset a running encode job back to queued.

Called on encoder service shutdown so that in-flight jobs are re-picked
up cleanly on the next start rather than being left stuck in "running".
"""

import logging

from common.models import EncodeJob, JobStatus

logger = logging.getLogger(__name__)


def rollback_encode_job(job_id: int, session_factory) -> None:
    """
    Reset a running EncodeJob back to queued, clearing all transient
    state so it will be picked up again by the next poll.
    """
    session = session_factory()
    try:
        job = session.get(EncodeJob, job_id)
        if job is None:
            logger.warning("EncodeJob %s not found during rollback", job_id)
            return
        job.status = JobStatus.queued
        job.started_at = None
        job.progress_percent = None
        job.progress_stage = None
        job.log = None
        session.commit()
        logger.info("EncodeJob %s rolled back to queued", job_id)
    except Exception:
        logger.exception("Failed to rollback EncodeJob %s", job_id)
    finally:
        session.close()
