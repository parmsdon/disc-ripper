"""
Discs API.

Phase 1: basic listing/detail/status. Rip/encode job creation and
metadata editing endpoints will be expanded in later phases.
"""

import os
import shutil
from pathlib import Path

from flask import Blueprint, jsonify, current_app, request
from sqlalchemy import and_, case, func, or_, select, update

from common.models import Catalog, CDTrack, Disc, DiscType, DiscStatus, Drive, JobStatus, RipJob, LookupCandidate, naive_utcnow
from ripper_service.region_patcher import patch_region_if_needed, MIN_ISO_SIZE

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
        "mb_medium_position": disc.mb_medium_position,
        "mb_medium_count": disc.mb_medium_count,
        "mb_medium_title": disc.mb_medium_title,
        "mb_release_id": disc.mb_release_id,
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

    _terminal = [DiscStatus.done, DiscStatus.error]
    discs = session.scalars(
        select(Disc)
        .where(
            Disc.temp_name.isnot(None),
            Disc.status.notin_(_terminal),
            or_(
                and_(Disc.type == DiscType.dvd, Disc.catalog_id.is_(None)),
                and_(Disc.type == DiscType.cd,  Disc.album_title.is_(None)),
            ),
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
            "mb_medium_position": disc.mb_medium_position,
            "mb_medium_count": disc.mb_medium_count,
            "mb_medium_title": disc.mb_medium_title,
            "mb_release_id": disc.mb_release_id,
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
    disc.mb_release_id = body.get("mb_release_id") or None
    if "temp_name" in body:
        disc.temp_name = body["temp_name"] or None

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


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


@discs_bp.route("/old-isos", methods=["GET"])
def list_old_isos():
    cfg = current_app.config["DISCRIPPER_CFG"]
    old_dir = Path(cfg["storage"]["datastore_root"]) / "dvd_store" / "old"
    if not old_dir.exists():
        return jsonify([])
    isos = sorted(old_dir.glob("*.iso"), key=lambda p: p.name.lower())
    result = []
    for iso in isos:
        size = iso.stat().st_size
        result.append({
            "filename": iso.name,
            "size_bytes": size,
            "size_display": _format_size(size),
            "is_valid": size >= MIN_ISO_SIZE,
        })
    return jsonify(result)


@discs_bp.route("/reconcile", methods=["POST"])
def reconcile_disc():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]

    body = request.get_json(silent=True) or {}
    drive_id = body.get("drive_id")
    disc_fingerprint = (body.get("disc_fingerprint") or "").strip()
    old_iso_filename = (body.get("old_iso_filename") or "").strip()
    temp_name = (body.get("temp_name") or "").strip() or None

    if not drive_id:
        return jsonify({"error": "Missing field: drive_id"}), 400
    if not disc_fingerprint:
        return jsonify({"error": "Missing field: disc_fingerprint"}), 400
    if not old_iso_filename:
        return jsonify({"error": "Missing field: old_iso_filename"}), 400

    drive = session.get(Drive, drive_id)
    if drive is None:
        return jsonify({"error": "Drive not found"}), 404

    datastore_root = Path(cfg["storage"]["datastore_root"])
    src = datastore_root / "dvd_store" / "old" / old_iso_filename
    if not src.exists():
        return jsonify({"error": f"ISO not found: {old_iso_filename}"}), 404

    now = naive_utcnow()

    existing = session.scalar(
        select(Disc)
        .where(Disc.disc_fingerprint == disc_fingerprint, Disc.type == DiscType.dvd)
        .order_by(Disc.created_at.asc())
        .limit(1)
    )

    if existing is not None:
        disc = existing
        disc.status = DiscStatus.ripped
        disc.temp_name = temp_name
        disc.drive_id = drive_id
        disc.ripped_at = now
        session.execute(
            update(RipJob)
            .where(RipJob.disc_id == disc.id, RipJob.status == JobStatus.queued)
            .values(status=JobStatus.done, error_message="Superseded by manual reconciliation")
        )
    else:
        disc = Disc(
            type=DiscType.dvd,
            status=DiscStatus.ripped,
            drive_id=drive_id,
            disc_fingerprint=disc_fingerprint,
            temp_name=temp_name,
            ripped_at=now,
        )
        session.add(disc)
        session.flush()

    dst_dir = datastore_root / "dvd_store" / "raw" / str(disc.id)
    dst = dst_dir / f"{disc_fingerprint}.iso"

    try:
        os.makedirs(str(dst_dir), exist_ok=True)
        shutil.move(str(src), str(dst))

        original_region = patch_region_if_needed(str(dst), disc.id)
        if original_region is not None:
            disc.ripped_in_region = f"0x{original_region:02X}"

        disc.raw_path = str(dst_dir.relative_to(datastore_root))
        session.commit()

        session.add(RipJob(
            disc_id=disc.id,
            drive_id=drive_id,
            status=JobStatus.done,
            started_at=now,
            completed_at=now,
        ))
        session.execute(
            update(Drive)
            .where(Drive.id == drive_id)
            .values(pending_action="eject", pending_action_requested_at=naive_utcnow())
        )
        session.commit()

        session.refresh(disc)
        return jsonify(_disc_to_dict(disc))

    except Exception as e:
        disc.status = DiscStatus.error
        disc.error_message = str(e)
        try:
            session.commit()
        except Exception:
            session.rollback()
        return jsonify({"error": str(e)}), 500


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
            "mb_release_id": data.get("mb_release_id"),
            "title": data.get("title"),
            "artist": data.get("artist"),
            "year": data.get("year"),
            "medium_position": data.get("medium_position"),
            "medium_count": data.get("medium_count"),
            "medium_title": data.get("medium_title"),
            "track_count": data.get("track_count"),
            "tracks": data.get("tracks", []),
        })
    return jsonify(result)


@discs_bp.route("/cd-catalogue", methods=["GET"])
def cd_catalogue():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    filter_param = request.args.get("filter", "all")
    search = request.args.get("search", "").strip()

    # Pre-fetch track counts and candidate counts in two GROUP BY queries
    # to avoid N+1 queries when building the response.
    track_rows = session.execute(
        select(
            CDTrack.disc_id,
            func.count().label("total"),
            func.count(CDTrack.title).label("titled"),
        ).group_by(CDTrack.disc_id)
    ).all()
    track_counts = {r.disc_id: (r.total, r.titled) for r in track_rows}

    cand_rows = session.execute(
        select(LookupCandidate.disc_id, func.count().label("cnt"))
        .group_by(LookupCandidate.disc_id)
    ).all()
    cand_counts = {r.disc_id: r.cnt for r in cand_rows}

    q = select(Disc).where(Disc.type == DiscType.cd)

    if filter_param == "identified":
        q = q.where(Disc.album_title.isnot(None))
    elif filter_param == "unidentified":
        q = q.where(Disc.album_title.is_(None))
    elif filter_param == "no_mb_match":
        q = q.where(Disc.mb_lookup_status == "not_found")
    elif filter_param == "mb_pending_error":
        q = q.where(Disc.mb_lookup_status.in_(["pending", "error"]))

    if search:
        q = q.where(
            Disc.temp_name.ilike(f"%{search}%")
            | Disc.disc_fingerprint.ilike(f"%{search}%")
            | Disc.album_title.ilike(f"%{search}%")
        )

    # Identified discs first (sorted by album_title), then unidentified (by temp_name).
    order_group = case((Disc.album_title.isnot(None), 0), else_=1)
    q = q.order_by(order_group, func.lower(Disc.album_title), func.lower(Disc.temp_name))

    discs = session.scalars(q).all()
    result = []
    for disc in discs:
        total, titled = track_counts.get(disc.id, (0, 0))
        result.append({
            "disc_id": disc.id,
            "disc_fingerprint": disc.disc_fingerprint,
            "disc_temp_name": disc.temp_name,
            "disc_ripped_at": disc.ripped_at.isoformat() if disc.ripped_at else None,
            "disc_rip_quality": disc.rip_quality,
            "disc_rip_attempt_count": disc.rip_attempt_count or 1,
            "album_title": disc.album_title,
            "album_artist": disc.album_artist,
            "track_count": total,
            "titled_tracks": titled,
            "mb_lookup_status": disc.mb_lookup_status,
            "mb_candidate_count": cand_counts.get(disc.id, 0),
            "identified": disc.album_title is not None,
        })
    return jsonify(result)


@discs_bp.route("/<int:disc_id>/retry-rip", methods=["POST"])
def retry_rip(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404

    if disc.status != DiscStatus.error and disc.rip_quality != "dirty":
        return jsonify({"error": "Disc is not in a retryable state (must be status=error or rip_quality=dirty)"}), 400

    disc.status = DiscStatus.queued
    disc.error_message = None
    disc.rip_quality = None
    disc.needs_rerip = False
    disc.rip_attempt_count = (disc.rip_attempt_count or 1) + 1

    if disc.type == DiscType.cd:
        for track in disc.tracks:
            track.rip_quality = None

    session.add(RipJob(disc_id=disc.id, drive_id=disc.drive_id, status=JobStatus.queued))
    session.commit()
    session.refresh(disc)
    return jsonify(_disc_to_dict(disc))


@discs_bp.route("/<int:disc_id>/cancel-rip", methods=["POST"])
def cancel_rip(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404

    if disc.status not in (DiscStatus.ripping, DiscStatus.building):
        return jsonify({"error": "Disc is not currently ripping or building"}), 400

    disc.status = DiscStatus.error
    disc.error_message = "Cancelled by user"

    active_job = next(
        (j for j in disc.rip_jobs if j.status in (JobStatus.queued, JobStatus.running)),
        None,
    )
    if active_job is not None:
        active_job.status = JobStatus.error
        active_job.error_message = "Cancelled by user"

    if disc.drive_id is not None:
        drive = session.get(Drive, disc.drive_id)
        if drive is not None:
            drive.pending_action = "eject"
            drive.pending_action_requested_at = naive_utcnow()

    session.commit()
    return jsonify({"status": "ok"})


@discs_bp.route("/<int:disc_id>", methods=["DELETE"])
def delete_disc(disc_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc = session.get(Disc, disc_id)
    if disc is None:
        return jsonify({"error": "Disc not found"}), 404

    if disc.temp_name:
        return jsonify({"error": "Cannot delete a named disc record"}), 400

    for obj in (
        list(disc.rip_jobs)
        + list(disc.lookup_candidates)
        + list(disc.tracks)
        + list(disc.encode_jobs)
    ):
        session.delete(obj)
    session.flush()
    session.delete(disc)
    session.commit()
    return jsonify({"status": "ok"})


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
