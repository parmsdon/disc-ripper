"""
Ripper service entry point.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m ripper_service.main

Polls configured drives for hardware identity and disc insert/removal.
Does NOT perform any ripping yet - that's a follow-up. Drives whose
physical region is unknown are detected but intentionally left alone;
read the region via the UI's "Read Region" action first.
"""

import logging
import os
import time

from common.config import load_config
from common.models import Drive
from ripper_service.db import get_session_factory
from ripper_service.drive_registry import sync_physical_drives
from ripper_service.pending_actions import process_pending_actions
from ripper_service.tray_status import get_tray_status
from ripper_service.udev_helper import get_drive_info

POLL_INTERVAL_SECONDS = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _tray_open_value(tray_status: str):
    if tray_status == "tray_open":
        return True
    if tray_status in ("disc_ok", "no_disc", "not_ready"):
        return False
    return None  # "unknown" or "no_info"


def run(cfg: dict) -> None:
    Session = get_session_factory(cfg)
    media_present_by_device = {}

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

                    info = get_drive_info(device_path)
                    media_present = info["media_present"]
                    media_type = info["media_type"]
                    tray_open = _tray_open_value(get_tray_status(device_path))

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
                    elif was_present and not media_present:
                        logger.info("Disc removed from %s", label)

                    media_present_by_device[device_path] = media_present

                    # Persist current media presence and tray state so the
                    # API/UI (which has no direct hardware access) can
                    # reflect them.
                    drive_id = state.get("drive_id")
                    if drive_id is not None:
                        drive = session.get(Drive, drive_id)
                        if drive is not None:
                            drive.media_present = media_present
                            drive.tray_open = tray_open

                session.commit()

            with Session() as session:
                process_pending_actions(session, cfg)

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
