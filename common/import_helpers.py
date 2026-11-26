"""
Bulk ISO import helpers for dvd_store/legacy/.
"""

import logging
import os
import re
from pathlib import Path

from sqlalchemy import select

from common.catalog_dups import normalise_catalog_title
from common.models import (
    Catalog, Disc, DiscStatus, DiscType, JobStatus, RipJob, naive_utcnow,
)
from common.encode_queue import create_encode_jobs
from ripper_service.disc_label import read_volume_info, GENERIC_VOLUME_LABELS
from ripper_service.region_patcher import patch_region_if_needed, MIN_ISO_SIZE

logger = logging.getLogger(__name__)

_REGION_MARKER_RE = re.compile(r'\s*\([A-Z0-9]+\)\s*$', re.IGNORECASE)
_YEAR_RE = re.compile(r'\[(\d{4})\]')
_NON_ALNUM_RE = re.compile(r'[^A-Za-z0-9 ]')


def parse_iso_filename(filename: str) -> tuple[str, int | None]:
    """
    'Danny, The Champion Of The World [1989] (R1).iso'
    -> ('DANNY_THE_CHAMPION_OF_THE_WORLD', 1989)
    """
    name = filename
    if name.lower().endswith('.iso'):
        name = name[:-4]
    name = _REGION_MARKER_RE.sub('', name).strip()
    year_m = _YEAR_RE.search(name)
    year = int(year_m.group(1)) if year_m else None
    name = _YEAR_RE.sub(' ', name).strip()
    name = _NON_ALNUM_RE.sub(' ', name)
    title_part = '_'.join(name.upper().split())
    return title_part, year


def make_working_title(title_part: str, year: int | None) -> str:
    return f"{title_part}_{year}" if year else title_part


def build_catalog_lookup(session) -> dict[str, list[dict]]:
    """
    Returns {working_title_key: [{id, title, year}, ...]} for all Catalog rows.
    Values are plain dicts so they survive session close.
    Call once per request / job run, then pass to find_catalog_match_from_lookup.
    """
    lookup: dict[str, list[dict]] = {}
    for c in session.scalars(select(Catalog)).all():
        key = normalise_catalog_title(c.title, c.year)
        lookup.setdefault(key, []).append({"id": c.id, "title": c.title, "year": c.year})
    return lookup


def find_catalog_match_from_lookup(
    catalog_lookup: dict[str, list[dict]],
    title_part: str,
    year: int | None,
) -> tuple[str, int | None, str | None]:
    """
    Returns ("exact"|"none"|"ambiguous", catalog_id|None, catalog_title|None).
    title_part is the raw parsed title (may contain underscores/spaces);
    normalise_catalog_title strips them before lookup so "ALIEN_3" matches "ALIEN3".
    """
    key = normalise_catalog_title(title_part, year)
    matches = catalog_lookup.get(key, [])
    if len(matches) == 1:
        m = matches[0]
        return "exact", m["id"], m["title"]
    if len(matches) > 1:
        return "ambiguous", None, None
    return "none", None, None


def import_one_iso(
    session,
    datastore_root: str,
    filename: str,
    dry_run: bool,
    catalog_lookup: dict[str, list[dict]],
) -> dict:
    """
    Import a single ISO from dvd_store/legacy/ (or retry a previously failed one).

    Idempotency:
    - imported_filename matches existing disc with status != error -> already_imported
    - imported_filename matches disc with status == error -> retry (reset and re-attempt)

    Order of operations (crash-safe):
    1. Create/reset Disc row, flush to get id, set raw_path, COMMIT.
    2. Create raw/ dir, move file (skip if already in dst), patch region.
    3. Set catalog_id, COMMIT.
    4. Create RipJob, create_encode_jobs, COMMIT.

    On any exception in step 2+: set disc.status=error, commit, return skipped.
    """
    legacy_dir = Path(datastore_root) / "dvd_store" / "legacy"
    src = legacy_dir / filename

    title_part, year = parse_iso_filename(filename)
    working_title = make_working_title(title_part, year)
    catalog_match, catalog_id, catalog_title = find_catalog_match_from_lookup(
        catalog_lookup, title_part, year
    )

    # Size check (file must exist in legacy/ OR already moved to raw/)
    src_size = None
    try:
        src_size = src.stat().st_size
    except FileNotFoundError:
        pass  # may have been moved to raw/ already on a previous attempt

    if src_size is not None and src_size < MIN_ISO_SIZE:
        return _result(filename, working_title, catalog_match, catalog_id, catalog_title,
                       "skipped", f"too small ({src_size // (1024 * 1024)} MB)")

    # Idempotency check
    existing = session.scalar(
        select(Disc).where(Disc.imported_filename == filename)
    )
    if existing is not None:
        if existing.status != DiscStatus.error:
            return _result(filename, working_title, catalog_match,
                           existing.catalog_id, catalog_title, "already_imported", None)
        disc = existing
        is_retry = True
    else:
        disc = None
        is_retry = False

    if dry_run:
        return _result(filename, working_title, catalog_match, catalog_id, catalog_title,
                       "imported", None)

    now = naive_utcnow()

    if is_retry:
        disc.status = DiscStatus.ripped
        disc.error_message = None
        disc.temp_name = working_title
        disc.ripped_at = now
        disc.imported = True
    else:
        disc = Disc(
            type=DiscType.dvd,
            status=DiscStatus.ripped,
            imported=True,
            imported_filename=filename,
            temp_name=working_title,
            ripped_at=now,
        )
        session.add(disc)

    # Phase 1: commit Disc row (with raw_path set) BEFORE moving file
    session.flush()
    raw_dir = Path(datastore_root) / "dvd_store" / "raw" / str(disc.id)
    disc.raw_path = str(raw_dir.relative_to(Path(datastore_root)))
    session.commit()

    # Phase 2: move file (or verify already moved), patch, link catalog, create jobs
    dst = raw_dir / f"{working_title}.iso"
    disc_id = disc.id  # capture before session may refresh

    try:
        os.makedirs(str(raw_dir), exist_ok=True)

        if not dst.exists():
            if not src.exists():
                raise FileNotFoundError(f"ISO not found in legacy/ or raw/: {filename}")
            os.rename(str(src), str(dst))

        try:
            original_region = patch_region_if_needed(str(dst), disc_id)
            if original_region is not None:
                disc.ripped_in_region = f"0x{original_region:02X}"
        except Exception:
            logger.warning("patch_region_if_needed failed for %s (disc #%s)",
                           filename, disc_id, exc_info=True)

        if not disc.volume_label:
            vol = read_volume_info(str(dst))
            raw_label = vol["volume_id"] or vol["volume_set_id"]
            disc.volume_label = (
                raw_label if raw_label and raw_label not in GENERIC_VOLUME_LABELS
                else working_title
            )

        if catalog_id is not None:
            disc.catalog_id = catalog_id
            disc.catalog_match_source = "auto"
        session.commit()

        rip_job = RipJob(
            disc_id=disc_id,
            drive_id=None,
            status=JobStatus.done,
            started_at=now,
            completed_at=now,
        )
        session.add(rip_job)
        session.commit()

        create_encode_jobs(session, disc_id, "dvd")

    except Exception as exc:
        logger.exception("Failed to import %s (disc #%s)", filename, disc_id)
        try:
            disc_row = session.get(Disc, disc_id)
            if disc_row is not None:
                disc_row.status = DiscStatus.error
                disc_row.error_message = str(exc)[:1000]
                session.commit()
        except Exception:
            session.rollback()
        return _result(filename, working_title, catalog_match, catalog_id, catalog_title,
                       "skipped", str(exc)[:200])

    return _result(filename, working_title, catalog_match, catalog_id, catalog_title,
                   "imported", None)


def _result(filename, working_title, catalog_match, catalog_id, catalog_title, status, reason):
    return {
        "filename": filename,
        "working_title": working_title,
        "catalog_match": catalog_match,
        "catalog_id": catalog_id,
        "catalog_title": catalog_title,
        "status": status,
        "reason": reason,
    }
