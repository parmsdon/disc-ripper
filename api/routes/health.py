"""
Health endpoints.

Phase 1: basic liveness + counts.
Later: DB Health tab data - unmatched discs, missing metadata, etc.
"""

from flask import Blueprint, jsonify, current_app
from sqlalchemy import select, func

from common.models import Disc, CDTrack, DiscType, DiscStatus

health_bp = Blueprint("health", __name__)


@health_bp.route("/", methods=["GET"])
def health():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    dvd_count = session.scalar(
        select(func.count()).select_from(Disc).where(Disc.type == DiscType.dvd)
    )
    cd_count = session.scalar(
        select(func.count()).select_from(Disc).where(Disc.type == DiscType.cd)
    )
    track_count = session.scalar(select(func.count()).select_from(CDTrack))

    unmatched_dvds = session.scalar(
        select(func.count()).select_from(Disc).where(
            Disc.type == DiscType.dvd,
            Disc.catalog_id.is_(None),
            Disc.status.in_([DiscStatus.identifying, DiscStatus.ripped, DiscStatus.encoding, DiscStatus.done]),
        )
    )

    needs_rerip = session.scalar(
        select(func.count()).select_from(Disc).where(Disc.needs_rerip.is_(True))
    )

    return jsonify({
        "status": "ok",
        "counts": {
            "dvds": dvd_count or 0,
            "cds": cd_count or 0,
            "cd_tracks": track_count or 0,
        },
        "db_health": {
            "unmatched_dvds": unmatched_dvds or 0,
            "discs_needing_rerip": needs_rerip or 0,
        },
    })
