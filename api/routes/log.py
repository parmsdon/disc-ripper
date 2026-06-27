"""
Activity log API — reads rip_log_events rows.
"""

from flask import Blueprint, jsonify, current_app, request
from sqlalchemy import func, select

from common.models import RipLogEvent

log_bp = Blueprint("log", __name__)


def _format_elapsed(seconds):
    if seconds is None:
        return None
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


def _event_to_dict(event: RipLogEvent) -> dict:
    return {
        "id": event.id,
        "occurred_at": event.occurred_at.isoformat(),
        "drive_label": event.drive_label,
        "disc_id": event.disc_id,
        "working_title": event.working_title,
        "track_number": event.track_number,
        "event_type": event.event_type,
        "outcome": event.outcome,
        "elapsed_seconds": event.elapsed_seconds,
        "elapsed_display": _format_elapsed(event.elapsed_seconds),
    }


@log_bp.route("/", methods=["GET"])
def get_log():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    try:
        limit = min(int(request.args.get("limit", 100)), 1000)
    except (ValueError, TypeError):
        limit = 100
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (ValueError, TypeError):
        offset = 0

    drive = request.args.get("drive", "").strip()
    event_type = request.args.get("event_type", "").strip()

    conditions = []
    if drive:
        conditions.append(RipLogEvent.drive_label == drive)
    if event_type:
        conditions.append(RipLogEvent.event_type == event_type)

    count_q = select(func.count()).select_from(RipLogEvent)
    data_q = select(RipLogEvent)
    if conditions:
        count_q = count_q.where(*conditions)
        data_q = data_q.where(*conditions)

    total = session.scalar(count_q) or 0
    events = session.scalars(
        data_q.order_by(RipLogEvent.occurred_at.desc()).offset(offset).limit(limit)
    ).all()

    drive_labels = session.scalars(
        select(RipLogEvent.drive_label)
        .where(RipLogEvent.drive_label.isnot(None))
        .distinct()
        .order_by(RipLogEvent.drive_label)
    ).all()

    return jsonify({
        "total": total,
        "events": [_event_to_dict(e) for e in events],
        "drive_labels": list(drive_labels),
    })
