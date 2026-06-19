"""
Drives API.

Phase 1: list configured drives and their current status, for the
"Drive Status" tab. Live progress (current disc, % complete, log
tail) will be populated once the ripper service writes job state.
"""

from flask import Blueprint, jsonify, current_app
from sqlalchemy import select

from common.models import Drive, Disc, DiscStatus, RipJob, JobStatus

drives_bp = Blueprint("drives", __name__)

_ACTIVE_STATUSES = [DiscStatus.queued, DiscStatus.ripping, DiscStatus.ripped, DiscStatus.encoding]


@drives_bp.route("/", methods=["GET"])
def list_drives():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]

    drives = session.scalars(select(Drive).where(Drive.env == cfg["environment"])).all()

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
            }

        result.append({
            "id": drive.id,
            "device_path": drive.device_path,
            "label": drive.label,
            "drive_type": drive.drive_type.value if drive.drive_type else None,
            "active": drive.active,
            "current_job": {
                "id": current_job.id,
                "disc_id": current_job.disc_id,
                "status": current_job.status.value,
                "started_at": current_job.started_at.isoformat() if current_job.started_at else None,
            } if current_job else None,
            "current_disc": current_disc,
        })

    return jsonify(result)
