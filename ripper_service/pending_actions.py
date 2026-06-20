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
        action = drive.pending_action

        try:
            if action == "read_region":
                _handle_read_region(drive, label)
            elif action == "eject":
                _handle_eject(drive, label)
            else:
                logger.warning("Drive %s has unknown pending_action %r - clearing", label, action)
        except Exception:
            # pending_action must always be cleared below, even if a handler
            # misbehaves - otherwise the drive gets stuck showing "in
            # progress" in the UI forever.
            logger.exception("Unexpected error processing pending_action %r for drive %s", action, label)
        finally:
            drive.pending_action = None
            drive.pending_action_requested_at = None
            session.commit()


def _handle_read_region(drive: Drive, label: str) -> None:
    result = read_region(drive.device_path)
    regions = result.get("regions")
    raw_output = result.get("raw_output")

    if regions is not None:
        if drive.physical_drive is None:
            logger.warning("Drive %s has no linked physical_drive - cannot record region", label)
        else:
            drive.physical_drive.region = regions
            drive.physical_drive.region_known = True
            logger.info("Region read for %s: region(s) %s", label, regions)
    else:
        # Covers both hard failures (no disc, regionset missing, nonzero
        # exit) and successful-but-unparseable output - either way there's
        # nothing to record automatically, so leave region_known unset and
        # surface raw_output for manual review.
        logger.warning("Region read failed for drive %s: %s", label, raw_output)
        if drive.physical_drive is not None:
            drive.physical_drive.notes = raw_output

    # Eject whatever was in the drive for the region read - it wasn't queued
    # for ripping. Harmless no-op if there was no disc to begin with.
    if not eject_drive(drive.device_path):
        logger.warning("Failed to eject %s after region read", label)


def _handle_eject(drive: Drive, label: str) -> None:
    if eject_drive(drive.device_path):
        logger.info("Ejected %s", label)
    else:
        logger.warning("Failed to eject %s", label)
