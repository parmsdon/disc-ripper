"""
Writes RipLogEvent rows to record ripping pipeline activity.

write_log_event() is fire-and-forget: it opens its own DB session,
commits a single row, and closes. It never raises — failures are
surfaced as warnings so the ripping pipeline is never interrupted.
"""

import logging

from common.models import RipLogEvent, naive_utcnow

logger = logging.getLogger(__name__)


def write_log_event(
    session_factory,
    event_type: str,
    *,
    drive_label: str = None,
    disc_id: int = None,
    working_title: str = None,
    track_number: int = None,
    outcome: str = None,
    elapsed_seconds: float = None,
) -> None:
    session = session_factory()
    try:
        session.add(RipLogEvent(
            occurred_at=naive_utcnow(),
            drive_label=drive_label,
            disc_id=disc_id,
            working_title=working_title,
            track_number=track_number,
            event_type=event_type,
            outcome=outcome,
            elapsed_seconds=elapsed_seconds,
        ))
        session.commit()
    except Exception:
        logger.warning(
            "Failed to write log event %r (disc_id=%s)",
            event_type, disc_id, exc_info=True,
        )
    finally:
        session.close()
