"""
Health endpoints — library statistics dashboard.
"""

from flask import Blueprint, jsonify, current_app
from sqlalchemy import exists, select, func

from common.models import (
    Catalog, CDTrack, Disc, DiscStatus, DiscType, EncodeJob, EncodeProfile,
    JobStatus, RipQuality,
)

health_bp = Blueprint("health", __name__)

_AWAITING_ID = [DiscStatus.ripped, DiscStatus.identifying]


@health_bp.route("/", methods=["GET"])
def health():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    def count(model, *where):
        q = select(func.count()).select_from(model)
        if where:
            q = q.where(*where)
        return session.scalar(q) or 0

    disc_exists = select(Disc.id).where(Disc.catalog_id == Catalog.id).correlate(Catalog).exists()
    matched_count = session.scalar(
        select(func.count()).select_from(Catalog).where(disc_exists)
    ) or 0
    catalog_total = count(Catalog)
    last_sync_dt = session.scalar(select(func.max(Catalog.synced_at)))
    last_sync = last_sync_dt.isoformat() if last_sync_dt else None

    def _encode_counts(media_type):
        rows = session.execute(
            select(EncodeJob.status, func.count().label("cnt"))
            .join(EncodeProfile, EncodeJob.profile_id == EncodeProfile.id)
            .where(EncodeProfile.media_type == media_type)
            .group_by(EncodeJob.status)
        ).all()
        counts = {"queued": 0, "running": 0, "complete": 0, "error": 0}
        for row in rows:
            sv = row.status.value if hasattr(row.status, "value") else str(row.status)
            key = "complete" if sv == "done" else sv
            if key in counts:
                counts[key] += row.cnt
        return counts

    return jsonify({
        "status": "ok",
        "library": {
            "dvd_count": count(Disc, Disc.type == DiscType.dvd),
            "cd_count": count(Disc, Disc.type == DiscType.cd),
            "cd_track_count": count(CDTrack),
        },
        "my_movies": {
            "catalog_count": catalog_total,
            "matched_to_ripped": matched_count,
            "never_ripped": catalog_total - matched_count,
            "last_sync": last_sync,
        },
        "identification": {
            "dvds_matched": count(Disc, Disc.type == DiscType.dvd, Disc.catalog_id.isnot(None)),
            "dvds_unmatched": count(
                Disc,
                Disc.type == DiscType.dvd,
                Disc.catalog_id.is_(None),
                Disc.status.in_(_AWAITING_ID),
            ),
            "cds_identified": count(Disc, Disc.type == DiscType.cd, Disc.album_title.isnot(None)),
            "cds_unidentified": count(
                Disc,
                Disc.type == DiscType.cd,
                Disc.album_title.is_(None),
                Disc.status.in_(_AWAITING_ID),
            ),
            "cd_tracks_titled": count(CDTrack, CDTrack.title.isnot(None)),
            "cd_tracks_untitled": count(CDTrack, CDTrack.title.is_(None)),
        },
        "quality": {
            "discs_needing_rerip": count(Disc, Disc.needs_rerip.is_(True)),
            "dirty_rips": count(Disc, Disc.rip_quality == "dirty"),
            "imperfect_tracks": count(
                CDTrack,
                CDTrack.rip_quality.in_([RipQuality.imperfect, RipQuality.failed]),
            ),
        },
        "musicbrainz": {
            "cds_mb_found": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "found"),
            "cds_mb_not_found": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "not_found"),
            "cds_mb_pending": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "pending"),
            "cds_mb_error": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "error"),
        },
        "pipeline": {
            "currently_ripping": count(Disc, Disc.status == DiscStatus.ripping),
            "currently_building": count(Disc, Disc.status == DiscStatus.building),
            "currently_identifying": count(Disc, Disc.status == DiscStatus.identifying),
            "error_discs": count(Disc, Disc.status == DiscStatus.error),
        },
        "dvd_encodes": _encode_counts("dvd"),
        "cd_encodes": _encode_counts("cd"),
    })


def _disc_row(disc):
    return {
        "disc_id": disc.id,
        "type": disc.type.value if hasattr(disc.type, "value") else str(disc.type),
        "temp_name": disc.temp_name or f"Disc #{disc.id}",
        "disc_fingerprint": disc.disc_fingerprint,
        "ripped_at": disc.ripped_at.isoformat() if disc.ripped_at else None,
        "error_message": disc.error_message,
        "created_at": disc.created_at.isoformat() if disc.created_at else None,
    }


@health_bp.route("/mb-not-found", methods=["GET"])
def mb_not_found():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.mb_lookup_status == "not_found",
        ).order_by(Disc.ripped_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/mb-error", methods=["GET"])
def mb_error():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.mb_lookup_status == "error",
        ).order_by(Disc.ripped_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/pipeline-identifying", methods=["GET"])
def pipeline_identifying():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(Disc.status == DiscStatus.identifying)
        .order_by(Disc.ripped_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/pipeline-errors", methods=["GET"])
def pipeline_errors():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(Disc.status == DiscStatus.error)
        .order_by(Disc.created_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])
