"""
Executes Drive.pending_action commands set via the API's DB-based
command queue (the "Read Region" / "Eject" actions in the UI).

Safe to call every poll iteration - drives with no pending_action are a
no-op.
"""

import logging
import time

from sqlalchemy import select

from common.models import Drive
from ripper_service.eject_helper import close_tray, eject_drive
from ripper_service.regionset_helper import read_region
from ripper_service.tray_status import get_tray_status, tray_open_from_status

logger = logging.getLogger(__name__)

_TRAY_CONFIRM_ATTEMPTS = 5
_TRAY_CONFIRM_INTERVAL_SECONDS = 1


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
    # The open/close decision is made here, at execution time, based on
    # the drive's current tray_open state - pending_action is still just
    # "eject" either way.
    closing = bool(drive.tray_open)

    if closing:
        if close_tray(drive.device_path):
            logger.info("Closed tray for %s", label)
        else:
            logger.warning("Failed to close tray for %s", label)
    else:
        if eject_drive(drive.device_path):
            logger.info("Ejected %s", label)
        else:
            logger.warning("Failed to eject %s", label)

    # Don't let the caller clear pending_action until the physical action
    # has actually finished - otherwise the UI flips back to "Eject"/
    # "Close Tray" for the few seconds before the next poll catches up.
    _confirm_tray_state(drive, expected_open=not closing, label=label)


def _confirm_tray_state(drive: Drive, expected_open: bool, label: str) -> None:
    """
    Poll get_tray_status() until it reflects expected_open, updating
    drive.tray_open with whatever is observed on each attempt. Gives up
    after _TRAY_CONFIRM_ATTEMPTS - the caller clears pending_action
    regardless, so the drive never gets stuck, but logs a warning since
    the UI will show a state that wasn't actually confirmed.
    """
    observed = None
    for _ in range(_TRAY_CONFIRM_ATTEMPTS):
        observed = tray_open_from_status(get_tray_status(drive.device_path))
        drive.tray_open = observed
        if observed == expected_open:
            logger.info("Confirmed tray_open=%s for %s", observed, label)
            return
        time.sleep(_TRAY_CONFIRM_INTERVAL_SECONDS)

    logger.warning(
        "Could not confirm tray state for %s within %d attempt(s) (last observed: %s) - "
        "clearing pending_action anyway",
        label, _TRAY_CONFIRM_ATTEMPTS, observed,
    )
