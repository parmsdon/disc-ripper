"""
Discs API.

Phase 1: basic listing/detail/status. Rip/encode job creation and
metadata editing endpoints will be expanded in later phases.
"""

from flask import Blueprint, jsonify, current_app, request
from sqlalchemy import and_, func, or_, select

from common.models import Catalog, CDTrack, Disc, DiscType, DiscStatus, Drive, JobStatus, LookupCandidate, naive_utcnow

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
        "rip_quality": disc.rip_quality,
        "rip_attempt_count": disc.rip_attempt_count,
        "scheduled_start": active_job.scheduled_start.isoformat() if active_job and active_job.scheduled_start else None,
        "progress_percent": active_job.progress_percent if active_job else None,
        "progress_stage": active_job.progress_stage if active_job else None,
        "mb_disc_id": disc.mb_disc_id,
        "mb_toc": disc.mb_toc,
        "mb_lookup_status": disc.mb_lookup_status,
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


@discs_bp.route("/identification-queue", methods=["GET"])
def identification_queue():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    discs = session.scalars(
        select(Disc)
        .where(
            or_(
                and_(
                    Disc.type == DiscType.dvd,
                    Disc.catalog_id.is_(None),
                    Disc.status.in_([DiscStatus.ripped, DiscStatus.identifying]),
                ),
                and_(
                    Disc.type == DiscType.cd,
                    Disc.album_title.is_(None),
                    Disc.status.in_([DiscStatus.ripped, DiscStatus.identifying]),
                ),
            )
        )
        .order_by(Disc.created_at.asc())
    ).all()

    result = []
    for disc in discs:
        candidate_count = session.scalar(
            select(func.count()).select_from(LookupCandidate)
            .where(LookupCandidate.disc_id == disc.id)
        )
        result.append({
            "id": disc.id,
            "type": disc.type.value,
            "status": disc.status.value,
            "temp_name": disc.temp_name,
            "disc_fingerprint": disc.disc_fingerprint,
            "created_at": disc.created_at.isoformat() if disc.created_at else None,
            "ripped_at": disc.ripped_at.isoformat() if disc.ripped_at else None,
            "mb_lookup_status": disc.mb_lookup_status if disc.type == DiscType.cd else None,
            "candidate_count": candidate_count or 0,
            "rip_quality": disc.rip_quality,
            "rip_attempt_count": disc.rip_attempt_count,
        })
    return jsonify(result)


@discs_bp.route("/<int:disc_id>/identify-dvd", methods=["PATCH"])
def identify_dvd(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404
    if disc.type != DiscType.dvd:
        return jsonify({"error": "Not a DVD"}), 400

    body = request.get_json(silent=True) or {}
    catalog_id = body.get("catalog_id")
    if catalog_id is None:
        return jsonify({"error": "Missing field: catalog_id"}), 400

    catalog = session.get(Catalog, catalog_id)
    if catalog is None:
        return jsonify({"error": "Catalog entry not found"}), 404

    already_matched = session.scalar(
        select(Disc.id).where(
            Disc.catalog_id == catalog_id,
            Disc.id != disc_id,
        ).limit(1)
    )
    if already_matched is not None:
        return jsonify({"error": "This catalog entry is already matched to another disc"}), 409

    disc.catalog_id = catalog_id
    session.commit()

    return jsonify({
        "id": disc.id,
        "type": disc.type.value,
        "status": disc.status.value,
        "temp_name": disc.temp_name,
        "catalog_id": disc.catalog_id,
        "catalog_title": catalog.title,
    })


@discs_bp.route("/<int:disc_id>/identify-cd", methods=["PATCH"])
def identify_cd(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404
    if disc.type != DiscType.cd:
        return jsonify({"error": "Not a CD"}), 400

    body = request.get_json(silent=True) or {}
    album_title = body.get("album_title")
    if not album_title:
        return jsonify({"error": "Missing field: album_title"}), 400

    disc.album_title = album_title
    disc.album_artist = body.get("album_artist") or None

    for track_data in body.get("tracks", []):
        track_id = track_data.get("id")
        if track_id is None:
            continue
        track = session.get(CDTrack, track_id)
        if track is not None and track.disc_id == disc_id:
            track.title = track_data.get("title") or None
            track.artist = track_data.get("artist") or None

    selected_candidate_id = body.get("selected_candidate_id")
    if selected_candidate_id is not None:
        for candidate in disc.lookup_candidates:
            candidate.selected = (candidate.id == selected_candidate_id)

    session.commit()
    return jsonify({"status": "ok"})


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
    if disc.temp_name and disc.status == DiscStatus.identifying:
        disc.status = DiscStatus.ripped
    session.commit()
    return jsonify(_disc_to_dict(disc))


@discs_bp.route("/<int:disc_id>/candidates", methods=["GET"])
def get_disc_candidates(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404

    result = []
    for candidate in sorted(disc.lookup_candidates, key=lambda c: c.id):
        data = candidate.candidate_data or {}
        result.append({
            "id": candidate.id,
            "source": candidate.source,
            "selected": candidate.selected,
            "title": data.get("title"),
            "artist": data.get("artist"),
            "year": data.get("year"),
            "track_count": data.get("track_count"),
            "tracks": data.get("tracks", []),
        })
    return jsonify(result)


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
    drive.pending_action_requested_at = naive_utcnow()
    session.commit()

    return jsonify({"status": "requested"})
