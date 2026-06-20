"""
Ripper service entry point.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m ripper_service.main

Polls configured drives for hardware identity and disc insert/removal,
creates rip jobs for newly-detected DVDs on region-known drives, and
starts queued jobs (dvdbackup, slot-aware up to max_rippers) once their
countdown elapses. CD ripping and the mkisofs/ISO-build step are not
implemented yet.
"""

import logging
import os
import time

from sqlalchemy import select

from common.config import load_config
from common.models import Disc, DiscStatus, DiscType, Drive, JobStatus, RipJob, Setting
from ripper_service.db import get_session_factory
from ripper_service.disc_label import read_volume_info
from ripper_service.drive_registry import sync_physical_drives
from ripper_service.job_rollback import rollback_excess_jobs
from ripper_service.job_starter import start_eligible_rip_jobs
from ripper_service.pending_actions import process_pending_actions
from ripper_service.tray_status import get_tray_status, tray_open_from_status
from ripper_service.udev_helper import get_drive_info

POLL_INTERVAL_SECONDS = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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

    logger.info("Ripper service started (env=%s)", cfg["environment"])

    while True:
        try:
            with Session() as session:
                drive_states = sync_physical_drives(session, cfg)

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
                        if not state.get("region_known", False):
                            logger.info(
                                "Drive %s has unknown region - disc inserted but will NOT be "
                                "processed for ripping. Use Read Region in the UI.",
                                label,
                            )
                        else:
                            logger.info("Disc detected in %s, type=%s", label, media_type)
                            if media_type == "dvd" and drive_id is not None:
                                volume_info = read_volume_info(device_path)
                                disc_fingerprint = volume_info["volume_id"] or volume_info["volume_set_id"]

                                disc = Disc(
                                    type=DiscType.dvd,
                                    status=DiscStatus.queued,
                                    drive_id=drive_id,
                                    disc_fingerprint=disc_fingerprint,
                                )
                                session.add(disc)
                                session.flush()

                                # scheduled_start stays null - the job only
                                # starts counting down once ripping_enabled
                                # is true (job_starter.py).
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
                            elif media_type != "dvd":
                                logger.info(
                                    "Drive %s: media_type=%s - skipping job creation "
                                    "(DVD ripping pipeline only for now)",
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

            with Session() as session:
                process_pending_actions(session, cfg)

            with Session() as session:
                start_eligible_rip_jobs(session, cfg, Session)

        except Exception:
            logger.exception("Error during poll iteration - continuing")

        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("Ripper service stopping (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
