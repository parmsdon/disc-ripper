"""
Syncs the My Movies 5 SQL Server catalog (tblTitles) into our `catalog`
table. My Movies is the read-only source of truth for DVD metadata; this
sync is one-way (My Movies -> our DB), never the reverse.
"""

import logging
import time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import get_db_url
from common.models import Catalog, MediaType, naive_utcnow
from mymovies_sync.connector import get_connection

logger = logging.getLogger(__name__)

_QUERY = """
    SELECT intId, nvcLocalTitle, nvcOriginalTitle, intProductionYear,
           nvcIMDB, nvcUPC, datUpdateAt
    FROM tblTitles
    ORDER BY intId
"""


def sync_catalog(cfg: dict, session) -> dict:
    """
    Pull every row from My Movies' tblTitles and upsert it into our
    catalog table, keyed on mymovies_id. Each row is committed
    individually so one bad row (counted in "errors") doesn't roll back
    the rest of the sync.

    media_type is always forced to MediaType.movie - nvcMediaType isn't
    reliably populated in this My Movies instance, and all titles in it
    are in fact movies.

    Returns {"synced": int, "inserted": int, "updated": int,
    "errors": int, "duration_seconds": float}.
    """
    start = time.monotonic()
    synced = inserted = updated = errors = 0

    conn = get_connection(cfg)
    try:
        cursor = conn.cursor()
        cursor.execute(_QUERY)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
    finally:
        conn.close()

    now = naive_utcnow()

    for row in rows:
        data = dict(zip(columns, row))
        try:
            mymovies_id = str(data["intId"])
            title = data.get("nvcLocalTitle") or data.get("nvcOriginalTitle")
            if not title:
                logger.warning("Skipping My Movies row intId=%s - no usable title", data.get("intId"))
                errors += 1
                continue

            raw_metadata = {
                key: (value.isoformat() if hasattr(value, "isoformat") else value)
                for key, value in data.items()
            }

            existing = session.scalars(
                select(Catalog).where(Catalog.mymovies_id == mymovies_id)
            ).first()

            if existing is None:
                existing = Catalog(mymovies_id=mymovies_id)
                session.add(existing)
                inserted += 1
            else:
                updated += 1

            existing.title = title
            existing.year = data.get("intProductionYear")
            existing.media_type = MediaType.movie
            existing.imdb_id = data.get("nvcIMDB")
            existing.upc = data.get("nvcUPC")
            existing.raw_metadata = raw_metadata
            existing.synced_at = now

            session.commit()
            synced += 1
        except Exception:
            logger.exception("Failed to sync My Movies row intId=%s", data.get("intId"))
            session.rollback()
            errors += 1

    return {
        "synced": synced,
        "inserted": inserted,
        "updated": updated,
        "errors": errors,
        "duration_seconds": time.monotonic() - start,
    }


def run_sync(cfg: dict) -> dict:
    """Entry point for both manual and periodic invocation - owns its own DB session."""
    engine = create_engine(get_db_url(cfg))
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        return sync_catalog(cfg, session)
    finally:
        session.close()


if __name__ == "__main__":
    import os
    from common.config import load_config

    logging.basicConfig(level=logging.INFO)
    result = run_sync(load_config(os.environ.get("DISCRIPPER_ENV")))
    print(result)
