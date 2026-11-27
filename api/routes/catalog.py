"""
Catalog API - browse the My Movies-synced catalog and trigger/monitor
a sync run. The catalog itself is read-only from the API's perspective
(My Movies is the source of truth); the only write path here is the
sync background thread.
"""

import logging
import threading
from datetime import timedelta

from flask import Blueprint, jsonify, current_app, request
from sqlalchemy import case, func, select, update

from common.catalog_dups import load_dup_roles
from common.config import get_db_url
from common.models import Catalog, Disc, DiscStatus, DiscType, SyncState, naive_utcnow
from mymovies_sync.sync import run_sync

logger = logging.getLogger(__name__)

catalog_bp = Blueprint("catalog", __name__)

# If running=TRUE and started_at is older than this, treat the lock as stale
# (the worker that claimed it must have died mid-sync).
_STALE_SYNC_MINUTES = 10


def _catalog_to_dict(entry: Catalog, dup_roles: dict | None = None) -> dict:
    dr = dup_roles.get(entry.id) if dup_roles else None
    return {
        "id": entry.id,
        "mymovies_id": entry.mymovies_id,
        "title": entry.title,
        "year": entry.year,
        "imdb_id": entry.imdb_id,
        "upc": entry.upc,
        "synced_at": entry.synced_at.isoformat() if entry.synced_at else None,
        "collection_number": entry.collection_number,
        "edition": entry.edition,
        "video_standard": entry.video_standard,
        "region_codes": entry.region_codes,
        "duplicate_role": dr.role if dr and dr.role != "sole" else None,
        "library_link_suffix": dr.link_suffix if dr else None,
    }


def _unmatched_filter():
    """SQLAlchemy WHERE clause that excludes catalog entries already matched to a disc."""
    return ~select(Disc.id).where(Disc.catalog_id == Catalog.id).exists()


def _fuzzy_order(search: str):
    """CASE expression: exact match first, then prefix, then anywhere."""
    low = search.lower()
    return case(
        (func.lower(Catalog.title) == low, 0),
        (Catalog.title.ilike(search + "%"), 1),
        else_=2,
    )


@catalog_bp.route("/unmatched-suggestions", methods=["GET"])
def unmatched_suggestions():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    title = request.args.get("title", "")
    limit = min(int(request.args.get("limit", 3)), 20)

    dup_roles = load_dup_roles(session)
    query = (
        select(Catalog)
        .where(Catalog.title.ilike(f"%{title}%"))
        .where(_unmatched_filter())
        .order_by(_fuzzy_order(title), Catalog.title)
        .limit(limit)
    )
    entries = session.scalars(query).all()
    return jsonify([_catalog_to_dict(e, dup_roles) for e in entries])


@catalog_bp.route("/", methods=["GET"])
def list_catalog():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    dup_roles = load_dup_roles(session)
    query = select(Catalog)

    search = request.args.get("search", "")
    if search:
        query = query.where(Catalog.title.ilike(f"%{search}%"))
        query = query.order_by(_fuzzy_order(search), Catalog.title)
    else:
        query = query.order_by(Catalog.title)

    if request.args.get("exclude_matched") == "true":
        query = query.where(_unmatched_filter())

    entries = session.scalars(query).all()
    return jsonify([_catalog_to_dict(e, dup_roles) for e in entries])


@catalog_bp.route("/<int:catalog_id>", methods=["GET"])
def get_catalog_entry(catalog_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    dup_roles = load_dup_roles(session)
    entry = session.get(Catalog, catalog_id)
    if entry is None:
        return jsonify({"error": "Catalog entry not found"}), 404

    return jsonify(_catalog_to_dict(entry, dup_roles))


def _run_sync_in_background(cfg: dict) -> None:
    """Background thread: run sync, writing progress and final result to sync_state."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(get_db_url(cfg))
    StateSession = sessionmaker(bind=engine)

    # Update progress at most every 5% of total rows (capped to every 50 rows).
    def _progress(current, total):
        step = max(1, min(50, total // 20))
        if current % step != 0 and current != total:
            return
        s = StateSession()
        try:
            s.execute(
                update(SyncState)
                .where(SyncState.id == 1)
                .values(progress_current=current, progress_total=total)
            )
            s.commit()
        except Exception:
            pass
        finally:
            s.close()

    s = StateSession()
    try:
        result = run_sync(cfg, progress_callback=_progress)
        s.execute(
            update(SyncState)
            .where(SyncState.id == 1)
            .values(
                running=False,
                finished_at=naive_utcnow(),
                progress_current=None,
                progress_total=None,
                last_synced=result.get("synced"),
                last_inserted=result.get("inserted"),
                last_updated=result.get("updated"),
                last_errors=result.get("errors"),
                last_duration_seconds=result.get("duration_seconds"),
                last_error_message=None,
            )
        )
        s.commit()
    except Exception as exc:
        logger.exception("My Movies sync (triggered via API) failed")
        try:
            s.execute(
                update(SyncState)
                .where(SyncState.id == 1)
                .values(
                    running=False,
                    finished_at=naive_utcnow(),
                    progress_current=None,
                    progress_total=None,
                    last_error_message=str(exc),
                )
            )
            s.commit()
        except Exception:
            pass
    finally:
        s.close()
        engine.dispose()


_RIPPED_STATUSES = [DiscStatus.ripped, DiscStatus.identifying, DiscStatus.done]
_IN_PROGRESS_STATUSES = [DiscStatus.queued, DiscStatus.ripping, DiscStatus.building, DiscStatus.error]


@catalog_bp.route("/dvd-catalogue", methods=["GET"])
def dvd_catalogue():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    # Primary three-param filter interface.
    rip_status = request.args.get("rip_status", "").strip() or None   # "ripped" | "unripped"
    id_status  = request.args.get("id_status",  "").strip() or None   # "identified" | "unidentified"
    mm_status  = request.args.get("mm_status",  "").strip() or None   # "matched" | "unmatched"
    search     = request.args.get("search", "").strip()
    dirty      = request.args.get("dirty", "").strip().lower() == "true"

    # Legacy single ?filter= shim so old bookmarks / callers still work.
    _legacy = request.args.get("filter", "").strip()
    if _legacy and not (rip_status or id_status or mm_status):
        if _legacy in ("matched", "ripped", "unmatched_rip"):
            rip_status = "ripped"
        elif _legacy == "unripped":
            rip_status = "unripped"
        elif _legacy == "identified":
            id_status = "identified"
        elif _legacy == "unidentified":
            id_status = "unidentified"

    sort_by = request.args.get("sort", "collection")
    rows = []
    dup_roles = load_dup_roles(session)

    def _search_matches_catalog(q):
        return q.where(Catalog.title.ilike(f"%{search}%")) if search else q

    def _search_matches_disc(q):
        if not search:
            return q
        return q.where(
            Disc.temp_name.ilike(f"%{search}%") | Disc.disc_fingerprint.ilike(f"%{search}%")
        )

    def _disc_fields(disc):
        return {
            "disc_id": disc.id,
            "disc_fingerprint": disc.disc_fingerprint,
            "disc_volume_label": disc.volume_label,
            "disc_temp_name": disc.temp_name,
            "disc_ripped_at": disc.ripped_at.isoformat() if disc.ripped_at else None,
            "disc_rip_quality": disc.rip_quality,
            "disc_status": disc.status.value if disc.status else None,
            "disc_rip_method": disc.rip_method,
            "disc_rip_reason": disc.rip_reason,
        }

    def _catalog_fields(catalog):
        dr = dup_roles.get(catalog.id)
        return {
            "catalog_id": catalog.id,
            "catalog_title": catalog.title,
            "catalog_year": catalog.year,
            "catalog_imdb_id": catalog.imdb_id,
            "catalog_collection_number": catalog.collection_number,
            "catalog_edition": catalog.edition,
            "catalog_duplicate_role": dr.role if dr and dr.role != "sole" else None,
            "catalog_link_suffix": dr.link_suffix if dr else None,
        }

    # ── matched rows: catalog joined to disc via catalog_id ──────────────────
    # mm_status="unmatched" excludes these (they have a catalog association).
    if mm_status in (None, "matched"):
        q = select(Catalog, Disc).join(Disc, Disc.catalog_id == Catalog.id).where(
            Disc.type == DiscType.dvd,
        )
        if rip_status == "ripped":
            q = q.where(Disc.status.in_(_RIPPED_STATUSES))
        elif rip_status == "unripped":
            q = q.where(Disc.status.in_(_IN_PROGRESS_STATUSES))
        elif rip_status == "protected":
            q = q.where(Disc.status == DiscStatus.protected)
        if id_status == "identified":
            q = q.where(Disc.temp_name.isnot(None))
        elif id_status == "unidentified":
            q = q.where(Disc.temp_name.is_(None))
        if dirty:
            q = q.where(Disc.rip_quality == "dirty")
        if search:
            q = q.where(
                Catalog.title.ilike(f"%{search}%")
                | Disc.temp_name.ilike(f"%{search}%")
                | Disc.disc_fingerprint.ilike(f"%{search}%")
            )
        for catalog, disc in session.execute(q.order_by(Catalog.title)):
            rows.append({"row_type": "matched", **_catalog_fields(catalog), **_disc_fields(disc)})

    # ── unripped rows: catalog entries with no disc yet ───────────────────────
    # These are My Movies entries (mm="matched") that are unripped and unidentified.
    # Exclude when any filter requires a disc, or when mm_status="unmatched".
    include_unripped = (
        not dirty and
        rip_status in (None, "unripped") and
        id_status in (None, "unidentified") and
        mm_status in (None, "matched") and
        rip_status != "protected"
    )
    if include_unripped:
        has_disc = select(Disc.id).where(Disc.catalog_id == Catalog.id).exists()
        q = _search_matches_catalog(
            select(Catalog).where(~has_disc).order_by(Catalog.title)
        )
        for catalog in session.scalars(q):
            rows.append({
                "row_type": "unripped",
                **_catalog_fields(catalog),
                "disc_id": None, "disc_fingerprint": None, "disc_temp_name": None,
                "disc_ripped_at": None, "disc_rip_quality": None, "disc_status": None,
            })

    # ── unmatched_rip rows: DVD discs with no catalog entry ───────────────────
    # mm_status="matched" excludes these (no catalog association).
    if mm_status in (None, "unmatched"):
        q = select(Disc).where(
            Disc.type == DiscType.dvd,
            Disc.catalog_id.is_(None),
        )
        if rip_status == "ripped":
            q = q.where(Disc.status.in_(_RIPPED_STATUSES))
        elif rip_status == "unripped":
            q = q.where(Disc.status.in_(_IN_PROGRESS_STATUSES))
        elif rip_status == "protected":
            q = q.where(Disc.status == DiscStatus.protected)
        if id_status == "identified":
            q = q.where(Disc.temp_name.isnot(None))
        elif id_status == "unidentified":
            q = q.where(Disc.temp_name.is_(None))
        if dirty:
            q = q.where(Disc.rip_quality == "dirty")
        for disc in session.scalars(_search_matches_disc(q).order_by(Disc.temp_name)):
            rows.append({
                "row_type": "unmatched_rip",
                "catalog_id": None, "catalog_title": None,
                "catalog_year": None, "catalog_imdb_id": None,
                "catalog_collection_number": None, "catalog_edition": None,
                "catalog_duplicate_role": None, "catalog_link_suffix": None,
                **_disc_fields(disc),
            })

    if sort_by == "collection":
        rows.sort(key=lambda r: (
            r.get("catalog_collection_number") is None,
            r.get("catalog_collection_number") or 0,
            (r.get("catalog_title") or "").lower(),
        ))

    return jsonify(rows)


@catalog_bp.route("/sync", methods=["POST"])
def trigger_sync():
    Session = current_app.config["DB_SESSION"]
    cfg = current_app.config["DISCRIPPER_CFG"]
    session = Session()

    stale_cutoff = naive_utcnow() - timedelta(minutes=_STALE_SYNC_MINUTES)

    # Atomic claim: flip running=TRUE only when not currently running (or lock is stale).
    # Postgres row-level locking ensures only one worker wins if two hit this simultaneously.
    result = session.execute(
        update(SyncState)
        .where(SyncState.id == 1)
        .where(
            (SyncState.running == False) |  # noqa: E712
            (SyncState.started_at < stale_cutoff)
        )
        .values(
            running=True,
            started_at=naive_utcnow(),
            progress_current=None,
            progress_total=None,
        )
        .returning(SyncState.id)
    )
    session.commit()

    if result.fetchone() is None:
        return jsonify({"status": "already_running"}), 409

    thread = threading.Thread(target=_run_sync_in_background, args=(cfg,), daemon=True)
    thread.start()
    return jsonify({"status": "started"})


@catalog_bp.route("/sync/status", methods=["GET"])
def sync_status():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    state = session.get(SyncState, 1)

    if state is None:
        return jsonify({
            "running": False,
            "last_result": None,
            "last_run_at": None,
            "progress": None,
        })

    last_result = None
    if state.finished_at is not None:
        if state.last_error_message:
            last_result = {"error": state.last_error_message}
        else:
            last_result = {
                "synced": state.last_synced,
                "inserted": state.last_inserted,
                "updated": state.last_updated,
                "errors": state.last_errors,
                "duration_seconds": state.last_duration_seconds,
            }

    return jsonify({
        "running": state.running,
        "last_result": last_result,
        "last_run_at": state.finished_at.isoformat() if state.finished_at else None,
        "progress": {"current": state.progress_current, "total": state.progress_total}
                    if state.running and state.progress_current is not None else None,
    })
