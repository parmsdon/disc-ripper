"""
Ripper service entry point.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m ripper_service.main

Polls configured drives for hardware identity and disc insert/removal,
creates rip jobs for newly-detected discs (DVDs gated on region_known,
CDs are not region-locked so always proceed), and starts queued jobs
(dvdbackup+mkisofs for DVD, cdparanoia per track for CD) slot-aware up
to max_rippers.

Exposes a simple remote start/stop protocol via the settings table:
    service_status:    "running" | "stopped" - written by this process
    service_heartbeat:  ISO timestamp, refreshed every poll iteration
                        and stamped one final time on clean shutdown
    service_command:    "" | "exit" - written by the API/UI, read and
                        cleared by this process on its next poll
A stale heartbeat while service_status="running" means the process died
without a clean shutdown (crash/kill -9), distinguishable in the UI from
a genuine clean stop.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from common.config import load_config
from common.models import CDTrack, Disc, DiscStatus, DiscType, Drive, JobStatus, RipJob, Setting
from ripper_service.cd_toc import read_table_of_contents
from ripper_service.mb_lookup import compute_mb_disc_id, lookup_musicbrainz
from ripper_service.db import get_session_factory
from ripper_service.disc_label import read_volume_info
from ripper_service.drive_registry import sync_physical_drives
from ripper_service.job_rollback import rollback_excess_jobs
from ripper_service.job_starter import start_eligible_rip_jobs
from ripper_service.pending_actions import process_pending_actions
from ripper_service.tray_status import get_tray_status, tray_open_from_status
from ripper_service.udev_helper import get_drive_info

POLL_INTERVAL_SECONDS = 3

# Statuses with an outstanding RipJob that job_starter can still act on.
# A disc that's "ripped"/"error" (rip phase concluded) shouldn't match -
# job_starter has nothing left to pick up for it, and reinserting that
# physical disc later (e.g. a deliberate re-rip) should get a fresh record.
_ACTIVE_DISC_STATUSES = (DiscStatus.queued, DiscStatus.ripping, DiscStatus.building)

# Statuses where the rip phase has concluded but the disc is still
# physically the same one we'd see again on reinsertion. needs_rerip
# distinguishes "dirty - reuse this row for a re-rip" (below) from
# "clean - already done, ignore reinsertion entirely".
_COMPLETED_DISC_STATUSES = (DiscStatus.ripped, DiscStatus.identifying)

_SERVICE_STATUS_KEY = "service_status"
_SERVICE_HEARTBEAT_KEY = "service_heartbeat"
_SERVICE_COMMAND_KEY = "service_command"


def _find_existing_disc(session, drive_id, disc_fingerprint):
    """
    Look up an existing Disc for (drive_id, disc_fingerprint) - shared by
    the DVD and CD detection branches below. An active disc (still has
    outstanding work) takes priority over a completed one. Returns
    (active_disc_or_None, completed_disc_or_None) - at most one is set.
    """
    if not disc_fingerprint:
        return None, None

    active = session.scalars(
        select(Disc)
        .where(
            Disc.drive_id == drive_id,
            Disc.disc_fingerprint == disc_fingerprint,
            Disc.status.in_(_ACTIVE_DISC_STATUSES),
        )
        .order_by(Disc.id)
    ).first()
    if active is not None:
        return active, None

    completed = session.scalars(
        select(Disc)
        .where(
            Disc.drive_id == drive_id,
            Disc.disc_fingerprint == disc_fingerprint,
            Disc.status.in_(_COMPLETED_DISC_STATUSES),
        )
        .order_by(Disc.id)
    ).first()
    return None, completed


def _reuse_for_rerip(session, disc, drive_id) -> int:
    """
    Reuse a previously-completed dirty Disc for a fresh attempt: reset
    to queued, bump the attempt count, clear the per-attempt fields, and
    queue a new RipJob against the same Disc (same id -> same output
    path, so a DVD's ISO/CD's WAVs get overwritten in place rather than
    duplicated). CDTrack rows are left as-is; job_starter re-rips only
    the ones still imperfect/failed.
    """
    disc.status = DiscStatus.queued
    disc.rip_attempt_count += 1
    disc.error_message = None
    disc.rip_quality = None
    session.add(RipJob(disc_id=disc.id, drive_id=drive_id, status=JobStatus.queued))
    return disc.rip_attempt_count


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _set_setting(session, key: str, value: str) -> None:
    setting = session.get(Setting, key)
    if setting:
        setting.value = value
    else:
        session.add(Setting(key=key, value=value))


def _get_setting_value(session, key: str, default: str) -> str:
    setting = session.get(Setting, key)
    return setting.value if setting else default


def shutdown(cfg: dict) -> None:
    """
    Clean shutdown: kill/roll back any in-flight rip jobs (subprocess
    terminated, scratch dir cleaned, job/disc reset to queued), mark the
    service stopped with a final heartbeat, and clear any pending
    command. Shared by both the remote "exit" command and Ctrl-C.
    """
    Session = get_session_factory(cfg)
    with Session() as session:
        rollback_excess_jobs(session, 0, cfg)

        _set_setting(session, _SERVICE_STATUS_KEY, "stopped")
        _set_setting(session, _SERVICE_HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())
        _set_setting(session, _SERVICE_COMMAND_KEY, "")

        session.commit()

    logger.info("Clean shutdown complete")


def run(cfg: dict) -> None:
    Session = get_session_factory(cfg)
    media_present_by_device = {}

    # /tmp-based scratch space may not survive a reboot - make sure it
    # exists before relying on it, rather than assuming manual setup.
    scratch_dir = cfg["storage"]["scratch_dir"]
    os.makedirs(scratch_dir, exist_ok=True)
    logger.info("Scratch directory ready: %s", scratch_dir)

    # Safety default: a restart should never silently resume mass-ripping,
    # regardless of whatever was last persisted.
    with Session() as session:
        setting = session.get(Setting, "ripping_enabled")
        if setting:
            setting.value = "false"
        else:
            session.add(Setting(key="ripping_enabled", value="false"))
        session.commit()
        logger.info("ripping_enabled reset to false on startup")

        # Any RipJob still "running" at startup belonged to a previous
        # instance that's gone - it can't actually still be running, so
        # clean it up via the same rollback mechanism used for manual stop.
        stale_running_count = len(session.scalars(
            select(RipJob).where(RipJob.status == JobStatus.running)
        ).all())
        if stale_running_count:
            logger.warning(
                "%d RipJob(s) found 'running' at startup - the process that "
                "owned them is gone, rolling them back",
                stale_running_count,
            )
            rollback_excess_jobs(session, 0, cfg)

    with Session() as session:
        _set_setting(session, _SERVICE_STATUS_KEY, "running")
        session.commit()
    logger.info("service_status set to running")

    logger.info("Ripper service started (env=%s)", cfg["environment"])

    while True:
        try:
            with Session() as session:
                drive_states = sync_physical_drives(session, cfg)

            pending_mb_lookups = []  # (mb_disc_id, mb_toc, db_disc_id) for CDs detected this poll
            with Session() as session:
                for drive_cfg in cfg.get("drives", []):
                    if not drive_cfg.get("active", True):
                        continue

                    device_path = drive_cfg["device"]
                    label = drive_cfg.get("label") or device_path
                    state = drive_states.get(device_path, {})
                    drive_id = state.get("drive_id")

                    info = get_drive_info(device_path)
                    media_present = info["media_present"]
                    media_type = info["media_type"]
                    tray_open = tray_open_from_status(get_tray_status(device_path))

                    was_present = media_present_by_device.get(device_path, False)

                    if media_present and not was_present:
                        logger.info("Disc detected in %s, type=%s", label, media_type)

                        if media_type == "dvd":
                            # Only DVDs are region-locked - CDs play anywhere,
                            # so they're handled below without this gate.
                            if not state.get("region_known", False):
                                logger.info(
                                    "Drive %s has unknown region - disc inserted but will NOT "
                                    "be processed for ripping. Use Read Region in the UI.",
                                    label,
                                )
                            elif drive_id is not None:
                                volume_info = read_volume_info(device_path)
                                disc_fingerprint = volume_info["volume_id"] or volume_info["volume_set_id"]

                                # A service restart loses media_present_by_device,
                                # so a disc that was never removed looks like a
                                # fresh insert on the next poll. Without an
                                # identifier we can't safely dedup, so fall back
                                # to the old create-new behavior in that case.
                                existing_active_disc, existing_completed_disc = _find_existing_disc(
                                    session, drive_id, disc_fingerprint,
                                )

                                if existing_active_disc is not None:
                                    logger.info(
                                        "Disc %s already tracked as disc #%s on Drive %s, "
                                        "resuming existing record",
                                        disc_fingerprint, existing_active_disc.id, label,
                                    )
                                elif existing_completed_disc is not None and existing_completed_disc.needs_rerip:
                                    attempt = _reuse_for_rerip(session, existing_completed_disc, drive_id)
                                    logger.info(
                                        "Disc %s previously had a dirty rip - starting re-rip "
                                        "attempt #%s for disc #%s",
                                        disc_fingerprint, attempt, existing_completed_disc.id,
                                    )
                                elif existing_completed_disc is not None:
                                    logger.info(
                                        "Disc %s already ripped cleanly as disc #%s on Drive %s - "
                                        "ignoring reinsertion",
                                        disc_fingerprint, existing_completed_disc.id, label,
                                    )
                                else:
                                    disc = Disc(
                                        type=DiscType.dvd,
                                        status=DiscStatus.queued,
                                        drive_id=drive_id,
                                        disc_fingerprint=disc_fingerprint,
                                    )
                                    session.add(disc)
                                    session.flush()

                                    # RipJob starts queued - job_starter promotes
                                    # it to running once ripping_enabled is true
                                    # and a slot is free.
                                    session.add(RipJob(
                                        disc_id=disc.id,
                                        drive_id=drive_id,
                                        status=JobStatus.queued,
                                    ))
                                    logger.info(
                                        "Created disc #%s for %s (volume_id=%s, volume_set_id=%s) - "
                                        "rip job queued, waiting for ripping to be enabled",
                                        disc.id, label, volume_info["volume_id"], volume_info["volume_set_id"],
                                    )

                        elif media_type == "cd":
                            if drive_id is not None:
                                toc = read_table_of_contents(device_path)
                                disc_fingerprint = toc["fingerprint"]

                                existing_active_disc, existing_completed_disc = _find_existing_disc(
                                    session, drive_id, disc_fingerprint,
                                )

                                if existing_active_disc is not None:
                                    logger.info(
                                        "Disc %s already tracked as disc #%s on Drive %s, "
                                        "resuming existing record",
                                        disc_fingerprint, existing_active_disc.id, label,
                                    )
                                elif existing_completed_disc is not None and existing_completed_disc.needs_rerip:
                                    attempt = _reuse_for_rerip(session, existing_completed_disc, drive_id)
                                    logger.info(
                                        "Disc %s previously had a dirty rip - starting re-rip "
                                        "attempt #%s for disc #%s",
                                        disc_fingerprint, attempt, existing_completed_disc.id,
                                    )
                                elif existing_completed_disc is not None:
                                    logger.info(
                                        "Disc %s already ripped cleanly as disc #%s on Drive %s - "
                                        "ignoring reinsertion",
                                        disc_fingerprint, existing_completed_disc.id, label,
                                    )
                                else:
                                    disc = Disc(
                                        type=DiscType.cd,
                                        status=DiscStatus.queued,
                                        drive_id=drive_id,
                                        disc_fingerprint=disc_fingerprint,
                                    )
                                    session.add(disc)
                                    session.flush()

                                    for track in toc["tracks"]:
                                        duration = track["length_sectors"] / 75.0 if track["length_sectors"] else None
                                        session.add(CDTrack(
                                            disc_id=disc.id,
                                            track_number=track["number"],
                                            duration_seconds=duration,
                                        ))

                                    session.add(RipJob(
                                        disc_id=disc.id,
                                        drive_id=drive_id,
                                        status=JobStatus.queued,
                                    ))

                                    mb_result = compute_mb_disc_id(device_path)
                                    if mb_result.get("disc_id"):
                                        disc.mb_disc_id = mb_result["disc_id"]
                                        disc.mb_toc = mb_result["toc"]
                                        disc.mb_lookup_status = "pending"
                                        pending_mb_lookups.append(
                                            (mb_result["disc_id"], mb_result["toc"], disc.id)
                                        )
                                    else:
                                        disc.mb_lookup_status = "error"
                                        logger.warning(
                                            "Failed to compute MB disc ID for disc #%s: %s",
                                            disc.id, mb_result.get("error"),
                                        )

                                    logger.info(
                                        "Created disc #%s for %s (CD, %s tracks, fingerprint=%s) - "
                                        "rip job queued, waiting for ripping to be enabled",
                                        disc.id, label, toc["track_count"], disc_fingerprint,
                                    )

                        else:
                            logger.info(
                                "Drive %s: media_type=%s - skipping job creation "
                                "(unsupported media type)",
                                label, media_type,
                            )
                    elif was_present and not media_present:
                        logger.info("Disc removed from %s", label)

                    media_present_by_device[device_path] = media_present

                    # Persist current media presence and tray state so the
                    # API/UI (which has no direct hardware access) can
                    # reflect them.
                    if drive_id is not None:
                        drive = session.get(Drive, drive_id)
                        if drive is not None:
                            drive.media_present = media_present
                            drive.tray_open = tray_open

                session.commit()

            for mb_disc_id, mb_toc, db_disc_id in pending_mb_lookups:
                t = threading.Thread(
                    target=lookup_musicbrainz,
                    args=(mb_disc_id, mb_toc, db_disc_id, Session),
                    daemon=True,
                )
                t.start()
                logger.info(
                    "MusicBrainz lookup started for disc #%s (MB disc ID: %s)",
                    db_disc_id, mb_disc_id,
                )

            with Session() as session:
                process_pending_actions(session, cfg)

            with Session() as session:
                start_eligible_rip_jobs(session, cfg, Session)

            with Session() as session:
                _set_setting(session, _SERVICE_HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat())
                session.commit()
                command = _get_setting_value(session, _SERVICE_COMMAND_KEY, "")

            if command == "exit":
                logger.info("Received exit command - shutting down cleanly")
                shutdown(cfg)
                return

        except Exception:
            logger.exception("Error during poll iteration - continuing")

        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("Ripper service stopping (KeyboardInterrupt)")
        shutdown(cfg)


if __name__ == "__main__":
    main()
