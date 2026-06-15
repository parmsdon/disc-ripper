"""
Drives API.

Phase 1: list configured drives and their current status, for the
"Drive Status" tab. Live progress (current disc, % complete, log
tail) will be populated once the ripper service writes job state.
"""

from flask import Blueprint, jsonify, current_app
from sqlalchemy import select

from common.models import Drive, RipJob, JobStatus

drives_bp = Blueprint("drives", __name__)


@drives_bp.route("/", methods=["GET"])
def list_drives():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]

    drives = session.scalars(select(Drive).where(Drive.env == cfg["environment"])).all()

    result = []
    for drive in drives:
        # Most recent rip job for this drive, if any
        current_job = session.scalars(
            select(RipJob)
            .where(RipJob.drive_id == drive.id)
            .where(RipJob.status.in_([JobStatus.queued, JobStatus.running]))
            .order_by(RipJob.created_at.desc())
        ).first()

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
        })

    return jsonify(result)
