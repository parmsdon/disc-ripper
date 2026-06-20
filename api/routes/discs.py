"""
Discs API.

Phase 1: basic listing/detail/status. Rip/encode job creation and
metadata editing endpoints will be expanded in later phases.
"""

from datetime import datetime

from flask import Blueprint, jsonify, current_app, request
from sqlalchemy import select

from common.models import Disc, DiscType, DiscStatus, CDTrack, Drive, JobStatus

discs_bp = Blueprint("discs", __name__)

_ACTIVE_JOB_STATUSES = (JobStatus.queued, JobStatus.running)


def _active_rip_job(disc: Disc):
    active_jobs = [j for j in disc.rip_jobs if j.status in _ACTIVE_JOB_STATUSES]
    if not active_jobs:
        return None
    return max(active_jobs, key=lambda j: j.created_at)


def _disc_to_dict(disc: Disc) -> dict:
    active_job = _active_rip_job(disc)
    return {
        "id": disc.id,
        "type": disc.type.value if disc.type else None,
        "status": disc.status.value if disc.status else None,
        "drive_id": disc.drive_id,
        "catalog_id": disc.catalog_id,
        "created_at": disc.created_at.isoformat() if disc.created_at else None,
        "ripped_at": disc.ripped_at.isoformat() if disc.ripped_at else None,
        "raw_path": disc.raw_path,
        "error_message": disc.error_message,
        "disc_fingerprint": disc.disc_fingerprint,
        "album_title": disc.album_title,
        "album_artist": disc.album_artist,
        "needs_rerip": disc.needs_rerip,
        "temp_name": disc.temp_name,
        "scheduled_start": active_job.scheduled_start.isoformat() if active_job and active_job.scheduled_start else None,
        "progress_percent": active_job.progress_percent if active_job else None,
        "progress_stage": active_job.progress_stage if active_job else None,
    }


def _track_to_dict(track: CDTrack) -> dict:
    return {
        "id": track.id,
        "disc_id": track.disc_id,
        "track_number": track.track_number,
        "title": track.title,
        "artist": track.artist,
        "duration_seconds": track.duration_seconds,
        "wav_filename": track.wav_filename,
        "rip_quality": track.rip_quality.value if track.rip_quality else None,
    }


@discs_bp.route("/", methods=["GET"])
def list_discs():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    query = select(Disc)

    disc_type = request.args.get("type")
    if disc_type in ("cd", "dvd"):
        query = query.where(Disc.type == DiscType(disc_type))

    status = request.args.get("status")
    if status:
        try:
            query = query.where(Disc.status == DiscStatus(status))
        except ValueError:
            return jsonify({"error": f"Invalid status '{status}'"}), 400

    discs = session.scalars(query.order_by(Disc.created_at.desc())).all()
    return jsonify([_disc_to_dict(d) for d in discs])


@discs_bp.route("/<int:disc_id>", methods=["GET"])
def get_disc(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404

    data = _disc_to_dict(disc)
    if disc.type == DiscType.cd:
        data["tracks"] = [_track_to_dict(t) for t in sorted(disc.tracks, key=lambda t: t.track_number)]

    return jsonify(data)


@discs_bp.route("/<int:disc_id>/temp-name", methods=["PATCH"])
def update_temp_name(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404

    body = request.get_json(silent=True) or {}
    if "temp_name" not in body:
        return jsonify({"error": "Missing field: temp_name"}), 400

    disc.temp_name = body["temp_name"] or None
    session.commit()
    return jsonify(_disc_to_dict(disc))


@discs_bp.route("/<int:disc_id>/eject", methods=["POST"])
def eject_disc(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404

    if disc.drive_id is None:
        return jsonify({"error": "Disc has no associated drive"}), 400

    drive = session.get(Drive, disc.drive_id)

    # Eject must run on the ripper machine where the drive is physically
    # attached - queue it via pending_action for the ripper service to pick
    # up on its next poll.
    drive.pending_action = "eject"
    drive.pending_action_requested_at = datetime.utcnow()
    session.commit()

    return jsonify({"status": "requested"})
