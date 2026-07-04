"""
Encode jobs API: read-only views of encode_jobs and encode_profiles,
plus aggregate stats for the encoder UI header.
"""

from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import select, func, case

from common.models import EncodeJob, EncodeProfile, JobStatus, Disc, CDTrack

encode_bp = Blueprint("encode", __name__)


def _job_to_dict(job: EncodeJob) -> dict:
    disc = job.disc
    track = job.track
    profile = job.profile
    return {
        "id": job.id,
        "disc_id": job.disc_id,
        "disc_temp_name": disc.temp_name if disc else None,
        "disc_type": disc.type.value if disc and disc.type else None,
        "profile_id": job.profile_id,
        "profile_name": profile.name if profile else None,
        "profile_media_type": profile.media_type if profile else None,
        "track_id": job.track_id,
        "track_number": track.track_number if track else None,
        "status": job.status.value,
        "progress_percent": job.progress_percent,
        "progress_stage": job.progress_stage,
        "input_path": job.source_file,
        "output_path": job.output_path,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
    }


@encode_bp.route("/jobs", methods=["GET"])
def get_jobs():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    status_filter = request.args.get("status")
    media_filter = request.args.get("media_type")
    disc_id_str = request.args.get("disc_id")

    stmt = select(EncodeJob).join(EncodeProfile, EncodeJob.profile_id == EncodeProfile.id)

    if status_filter:
        try:
            stmt = stmt.where(EncodeJob.status == JobStatus(status_filter))
        except ValueError:
            return jsonify({"error": f"Invalid status: {status_filter}"}), 400

    if media_filter:
        stmt = stmt.where(EncodeProfile.media_type == media_filter)

    if disc_id_str:
        try:
            stmt = stmt.where(EncodeJob.disc_id == int(disc_id_str))
        except ValueError:
            return jsonify({"error": "disc_id must be an integer"}), 400

    # running (started_at ASC) → queued (created_at ASC) → done/error (completed_at DESC)
    stmt = stmt.order_by(
        case(
            (EncodeJob.status == JobStatus.running, 1),
            (EncodeJob.status == JobStatus.queued, 2),
            else_=3,
        ),
        case((EncodeJob.status == JobStatus.running, EncodeJob.started_at)).asc(),
        case((EncodeJob.status == JobStatus.queued, EncodeJob.created_at)).asc(),
        case(
            (EncodeJob.status.notin_([JobStatus.running, JobStatus.queued]), EncodeJob.completed_at)
        ).desc().nullslast(),
    )

    jobs = session.scalars(stmt).all()
    return jsonify([_job_to_dict(j) for j in jobs])


@encode_bp.route("/stats", methods=["GET"])
def get_stats():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    rows = session.execute(
        select(
            EncodeProfile.media_type,
            EncodeJob.status,
            func.count(EncodeJob.id).label("n"),
        )
        .join(EncodeProfile, EncodeJob.profile_id == EncodeProfile.id)
        .group_by(EncodeProfile.media_type, EncodeJob.status)
    ).all()

    stats = {
        "dvd": {"queued": 0, "running": 0, "done": 0, "error": 0},
        "cd":  {"queued": 0, "running": 0, "done": 0, "error": 0},
    }
    for media_type, status, n in rows:
        if media_type in stats and status.value in stats[media_type]:
            stats[media_type][status.value] = n

    return jsonify(stats)


@encode_bp.route("/profiles", methods=["GET"])
def get_profiles():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    profiles = session.scalars(
        select(EncodeProfile).order_by(EncodeProfile.display_order)
    ).all()

    return jsonify([
        {
            "id": p.id,
            "name": p.name,
            "media_type": p.media_type,
            "tool": p.tool,
            "output_folder": p.output_folder,
            "enabled": p.enabled,
            "display_order": p.display_order,
            "depends_on_profile_id": p.depends_on_profile_id,
        }
        for p in profiles
    ])
