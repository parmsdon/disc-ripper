"""
Executes Drive.pending_action commands set via the API's DB-based
command queue (the "Read Region" / "Eject" actions in the UI).

Safe to call every poll iteration - drives with no pending_action are a
no-op.
"""

import logging

from sqlalchemy import select

from common.models import Drive
from ripper_service.eject_helper import eject_drive
from ripper_service.regionset_helper import read_region

logger = logging.getLogger(__name__)


def process_pending_actions(session, cfg: dict) -> None:
    env = cfg["environment"]

    drives = session.scalars(
        select(Drive).where(Drive.env == env, Drive.pending_action.is_not(None))
    ).all()

    for drive in drives:
        label = drive.label or drive.device_path

        if drive.pending_action == "read_region":
            _handle_read_region(drive, label)
        elif drive.pending_action == "eject":
            _handle_eject(drive, label)
        else:
            logger.warning("Drive %s has unknown pending_action %r - clearing", label, drive.pending_action)

        drive.pending_action = None
        drive.pending_action_requested_at = None
        session.commit()


def _handle_read_region(drive: Drive, label: str) -> None:
    result = read_region(drive.device_path)
    region = result.get("region")

    if drive.physical_drive is None:
        logger.warning("Drive %s has no linked physical_drive - cannot record region", label)
    elif region is not None:
        drive.physical_drive.region = region
        drive.physical_drive.region_known = True
        logger.info("Region read for %s: region %s", label, region)
    else:
        logger.warning(
            "Could not determine a single region for %s - leaving region_known unset "
            "for manual review. Raw output: %s",
            label, result.get("raw_output"),
        )
        drive.physical_drive.notes = result.get("raw_output")

    # The disc used for the region read wasn't queued for ripping - eject it.
    if not eject_drive(drive.device_path):
        logger.warning("Failed to eject %s after region read", label)


def _handle_eject(drive: Drive, label: str) -> None:
    if eject_drive(drive.device_path):
        logger.info("Ejected %s", label)
    else:
        logger.warning("Failed to eject %s", label)
