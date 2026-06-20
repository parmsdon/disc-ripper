"""
Drives API.

Phase 1: list configured drives and their current status, for the
"Drive Status" tab. Live progress (current disc, % complete, log
tail) will be populated once the ripper service writes job state.
"""

from datetime import datetime

from flask import Blueprint, jsonify, current_app
from sqlalchemy import select

from common.models import Drive, Disc, DiscStatus, RipJob, JobStatus

drives_bp = Blueprint("drives", __name__)

_ACTIVE_STATUSES = [DiscStatus.queued, DiscStatus.ripping, DiscStatus.ripped, DiscStatus.encoding]


def _drive_summary(drive: Drive) -> dict:
    return {
        "id": drive.id,
        "device_path": drive.device_path,
        "label": drive.label,
        "drive_type": drive.drive_type.value if drive.drive_type else None,
        "active": drive.active,
        "region": drive.physical_drive.region if drive.physical_drive else None,
        "region_known": drive.physical_drive.region_known if drive.physical_drive else False,
        "pending_action": drive.pending_action,
        "media_present": drive.media_present,
        "tray_open": drive.tray_open,
    }


@drives_bp.route("/", methods=["GET"])
def list_drives():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]

    drives = session.scalars(
        select(Drive).where(Drive.env == cfg["environment"]).order_by(Drive.id)
    ).all()

    result = []
    for drive in drives:
        # Most recent active rip job for this drive
        current_job = session.scalars(
            select(RipJob)
            .where(RipJob.drive_id == drive.id)
            .where(RipJob.status.in_([JobStatus.queued, JobStatus.running]))
            .order_by(RipJob.created_at.desc())
        ).first()

        # Most recent disc for this drive in an active (non-terminal) status
        current_disc_row = session.scalars(
            select(Disc)
            .where(Disc.drive_id == drive.id)
            .where(Disc.status.in_(_ACTIVE_STATUSES))
            .order_by(Disc.created_at.desc())
        ).first()

        current_disc = None
        if current_disc_row:
            current_disc = {
                "id": current_disc_row.id,
                "type": current_disc_row.type.value if current_disc_row.type else None,
                "status": current_disc_row.status.value if current_disc_row.status else None,
                "temp_name": current_disc_row.temp_name,
                "ripped_at": current_disc_row.ripped_at.isoformat() if current_disc_row.ripped_at else None,
                "created_at": current_disc_row.created_at.isoformat() if current_disc_row.created_at else None,
                "scheduled_start": current_job.scheduled_start.isoformat() if current_job and current_job.scheduled_start else None,
            }

        drive_dict = _drive_summary(drive)
        drive_dict["current_job"] = {
            "id": current_job.id,
            "disc_id": current_job.disc_id,
            "status": current_job.status.value,
            "started_at": current_job.started_at.isoformat() if current_job.started_at else None,
        } if current_job else None
        drive_dict["current_disc"] = current_disc

        result.append(drive_dict)

    return jsonify(result)


@drives_bp.route("/<int:drive_id>/region/start-read", methods=["POST"])
def start_region_read(drive_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    drive = session.get(Drive, drive_id)
    if drive is None:
        return jsonify({"error": "Drive not found"}), 404

    # Actual region reading happens in the ripper service - it picks this
    # up via the pending_action command queue on its next poll.
    drive.pending_action = "read_region"
    drive.pending_action_requested_at = datetime.utcnow()
    session.commit()

    return jsonify(_drive_summary(drive))


@drives_bp.route("/<int:drive_id>/eject", methods=["POST"])
def eject_drive_directly(drive_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    drive = session.get(Drive, drive_id)
    if drive is None:
        return jsonify({"error": "Drive not found"}), 404

    # Same command-queue mechanism as discs.py's eject endpoint, but keyed
    # directly off drive_id - works even when there's no disc record at all
    # (e.g. an empty drive being opened, or a disc never queued for ripping).
    drive.pending_action = "eject"
    drive.pending_action_requested_at = datetime.utcnow()
    session.commit()

    return jsonify(_drive_summary(drive))


@drives_bp.route("/<int:drive_id>/region/reread", methods=["POST"])
def reread_region(drive_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    drive = session.get(Drive, drive_id)
    if drive is None:
        return jsonify({"error": "Drive not found"}), 404

    if drive.physical_drive is not None:
        drive.physical_drive.region = None
        drive.physical_drive.region_known = False
        session.commit()

    return jsonify(_drive_summary(drive))
