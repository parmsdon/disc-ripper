"""
Encoder service entry point.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m encoder_service.main

Polls encode_jobs for queued work, respects dvd_encoding_enabled /
cd_encoding_enabled and max_dvd_encoders / max_cd_encoders settings,
and enforces dependency ordering (a job whose profile depends_on another
profile is not started until that profile's job is done on the same disc).

CD encoding (FLAC via flac, MP3 via ffmpeg/libmp3lame) is implemented.
DVD encoding is stubbed and will be implemented in the DVD encoding phase.

Uses separate settings keys from ripper_service to avoid collisions:
    encoder_service_status:     "running" | "stopped"
    encoder_service_heartbeat:  ISO timestamp, refreshed every poll
    encoder_service_command:    "" | "exit"
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import load_config, get_db_url
from common.encode_queue import get_next_encode_jobs
from common.models import EncodeJob, JobStatus, Setting
from encoder_service.cd_encoder import encode_cd_track
from encoder_service.job_rollback import rollback_encode_job

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


def shutdown(Session: sessionmaker) -> None:
    """
    Roll back all running encode jobs (DB query is the source of truth —
    we don't rely on in-memory thread state so this is safe on KeyboardInterrupt
    too), mark service stopped, write final heartbeat.
    """
    # Find running jobs via DB, then reset each via the canonical rollback path.
    with Session() as session:
        running_ids = [
            job.id for job in session.scalars(
                select(EncodeJob).where(EncodeJob.status == JobStatus.running)
            ).all()
        ]

    for job_id in running_ids:
        rollback_encode_job(job_id, Session)

    with Session() as session:
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

    # {job_id: Thread} for each dispatched CD encode job.
    # Reaped each poll by checking thread.is_alive().
    running_cd_jobs: dict[int, threading.Thread] = {}

    while True:
        try:
            # Reap threads that finished since the last poll.
            finished = [jid for jid, t in running_cd_jobs.items() if not t.is_alive()]
            for jid in finished:
                del running_cd_jobs[jid]

            with Session() as session:
                dvd_enabled = _get_bool(session, _DVD_ENCODING_ENABLED_KEY, False)
                cd_enabled = _get_bool(session, _CD_ENCODING_ENABLED_KEY, False)
                max_dvd = _get_int(session, _MAX_DVD_ENCODERS_KEY, _DEFAULT_MAX_DVD_ENCODERS)
                max_cd = _get_int(session, _MAX_CD_ENCODERS_KEY, _DEFAULT_MAX_CD_ENCODERS)

                # --- CD encoding ---
                if cd_enabled:
                    slots_free = max_cd - len(running_cd_jobs)
                    if slots_free > 0:
                        cd_jobs = get_next_encode_jobs(
                            session, "cd", slots_free, list(running_cd_jobs.keys())
                        )
                        for job in cd_jobs:
                            thread = threading.Thread(
                                target=encode_cd_track,
                                args=(job.id, Session),
                                daemon=True,
                            )
                            thread.start()
                            running_cd_jobs[job.id] = thread
                            logger.info(
                                "Started CD encode job %s (disc #%s, track_id=%s, profile_id=%s)",
                                job.id, job.disc_id, job.track_id, job.profile_id,
                            )

                # --- DVD encoding (not yet implemented) ---
                if dvd_enabled:
                    dvd_jobs = get_next_encode_jobs(session, "dvd", max_dvd, [])
                    for job in dvd_jobs:
                        logger.info(
                            "TODO: DVD encode job %s (disc #%s, profile_id=%s) - not yet implemented",
                            job.id, job.disc_id, job.profile_id,
                        )

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
