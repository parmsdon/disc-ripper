"""
Encoder service entry point.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m encoder_service.main

Polls encode_jobs for queued work, respects dvd_encoding_enabled /
cd_encoding_enabled and max_dvd_encoders / max_cd_encoders settings,
and enforces dependency ordering (a job whose profile depends_on another
profile is not started until that profile's job is done on the same disc).

Uses separate settings keys from ripper_service to avoid collisions:
    encoder_service_status:     "running" | "stopped"
    encoder_service_heartbeat:  ISO timestamp, refreshed every poll
    encoder_service_command:    "" | "exit"

Actual encode workers (handbrake / ffmpeg / flac subprocess wrappers) are
not yet implemented - this skeleton logs the jobs it would start and leaves
a clear hook (start_encode_worker) for Phase 3.
"""

import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import load_config, get_db_url
from common.encode_queue import get_next_encode_jobs
from common.models import EncodeJob, JobStatus, Setting

POLL_INTERVAL_SECONDS = 5

_STATUS_KEY = "encoder_service_status"
_HEARTBEAT_KEY = "encoder_service_heartbeat"
_COMMAND_KEY = "encoder_service_command"

_DVD_ENCODING_ENABLED_KEY = "dvd_encoding_enabled"
_CD_ENCODING_ENABLED_KEY = "cd_encoding_enabled"
_MAX_DVD_ENCODERS_KEY = "max_dvd_encoders"
_MAX_CD_ENCODERS_KEY = "max_cd_encoders"
_DEFAULT_MAX_DVD_ENCODERS = 1
_DEFAULT_MAX_CD_ENCODERS = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _get_session_factory(cfg: dict) -> sessionmaker:
    engine = create_engine(get_db_url(cfg))
    return sessionmaker(bind=engine)


def _set_setting(session, key: str, value: str) -> None:
    setting = session.get(Setting, key)
    if setting:
        setting.value = value
    else:
        session.add(Setting(key=key, value=value))


def _get_bool(session, key: str, default: bool) -> bool:
    setting = session.get(Setting, key)
    return setting.value == "true" if setting else default


def _get_int(session, key: str, default: int) -> int:
    setting = session.get(Setting, key)
    if setting is None:
        return default
    try:
        return int(setting.value)
    except ValueError:
        return default


def _get_command(session) -> str:
    setting = session.get(Setting, _COMMAND_KEY)
    return setting.value if setting else ""


def start_encode_worker(job: EncodeJob) -> None:
    """
    Placeholder hook for Phase 3: spawn a worker thread that runs
    handbrake / ffmpeg / flac for the given EncodeJob and updates
    job.status / progress_percent / progress_stage / log on the fly.
    """
    raise NotImplementedError("Encode workers not yet implemented")


def shutdown(Session: sessionmaker) -> None:
    """
    Roll back any stale running jobs, mark service stopped, write final heartbeat.
    """
    with Session() as session:
        stale = session.scalars(
            select(EncodeJob).where(EncodeJob.status == JobStatus.running)
        ).all()
        for job in stale:
            job.status = JobStatus.queued
            job.started_at = None
            job.progress_percent = None
            job.progress_stage = None

        _set_setting(session, _STATUS_KEY, "stopped")
        _set_setting(session, _HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())
        _set_setting(session, _COMMAND_KEY, "")
        session.commit()

    logger.info("Encoder service shutdown complete")


def run(cfg: dict) -> None:
    Session = _get_session_factory(cfg)

    # Roll back any stale running jobs left by a previous crashed instance.
    with Session() as session:
        stale = session.scalars(
            select(EncodeJob).where(EncodeJob.status == JobStatus.running)
        ).all()
        if stale:
            logger.warning(
                "%d EncodeJob(s) found 'running' at startup - rolling back to queued",
                len(stale),
            )
            for job in stale:
                job.status = JobStatus.queued
                job.started_at = None
                job.progress_percent = None
                job.progress_stage = None
            session.commit()

        _set_setting(session, _STATUS_KEY, "running")
        session.commit()

    logger.info("Encoder service started (env=%s)", cfg["environment"])

    # IDs of EncodeJobs dispatched to worker threads; guards against
    # double-dispatch across poll iterations.
    running_job_ids: set[int] = set()

    while True:
        try:
            with Session() as session:
                dvd_enabled = _get_bool(session, _DVD_ENCODING_ENABLED_KEY, False)
                cd_enabled = _get_bool(session, _CD_ENCODING_ENABLED_KEY, False)
                max_dvd = _get_int(session, _MAX_DVD_ENCODERS_KEY, _DEFAULT_MAX_DVD_ENCODERS)
                max_cd = _get_int(session, _MAX_CD_ENCODERS_KEY, _DEFAULT_MAX_CD_ENCODERS)

                for media_type, enabled, max_slots in (
                    ("dvd", dvd_enabled, max_dvd),
                    ("cd", cd_enabled, max_cd),
                ):
                    if not enabled:
                        continue

                    active_count = len(running_job_ids)
                    if active_count >= max_slots:
                        continue

                    jobs = get_next_encode_jobs(
                        session,
                        media_type,
                        max_slots - active_count,
                        list(running_job_ids),
                    )
                    for job in jobs:
                        logger.info(
                            "TODO: start encode job %s (disc #%s, profile_id=%s, track_id=%s)",
                            job.id, job.disc_id, job.profile_id, job.track_id,
                        )
                        # Phase 3: uncomment when workers are ready.
                        # start_encode_worker(job)
                        # running_job_ids.add(job.id)

            with Session() as session:
                _set_setting(session, _HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())
                session.commit()
                command = _get_command(session)

            if command == "exit":
                logger.info("Received exit command - shutting down")
                shutdown(Session)
                return

        except Exception:
            logger.exception("Error during encoder poll iteration - continuing")

        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("Encoder service stopping (KeyboardInterrupt)")
        shutdown(_get_session_factory(cfg))


if __name__ == "__main__":
    main()
