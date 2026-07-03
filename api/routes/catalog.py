"""
Catalog API - browse the My Movies-synced catalog and trigger/monitor
a sync run. The catalog itself is read-only from the API's perspective
(My Movies is the source of truth); the only write path here is the
sync background thread.
"""

import logging
import threading
from datetime import datetime, timezone

from flask import Blueprint, jsonify, current_app, request
from sqlalchemy import case, func, select

from common.models import Catalog, Disc, DiscStatus, DiscType
from mymovies_sync.sync import run_sync

logger = logging.getLogger(__name__)

catalog_bp = Blueprint("catalog", __name__)

# Process-wide sync state, shared by the background thread and the
# status endpoint. A single Flask process (dev: `flask run`/app.py) is
# assumed - this isn't safe across multiple worker processes, same
# caveat as the ripper service's in-process active_jobs registry.
_sync_lock = threading.Lock()
_sync_running = False
_last_result = None
_last_run_at = None
_sync_progress = None  # {"current": int, "total": int} while running, None otherwise


def _catalog_to_dict(entry: Catalog) -> dict:
    return {
        "id": entry.id,
        "mymovies_id": entry.mymovies_id,
        "title": entry.title,
        "year": entry.year,
        "imdb_id": entry.imdb_id,
        "upc": entry.upc,
        "synced_at": entry.synced_at.isoformat() if entry.synced_at else None,
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

    query = (
        select(Catalog)
        .where(Catalog.title.ilike(f"%{title}%"))
        .where(_unmatched_filter())
        .order_by(_fuzzy_order(title), Catalog.title)
        .limit(limit)
    )
    entries = session.scalars(query).all()
    return jsonify([_catalog_to_dict(e) for e in entries])


@catalog_bp.route("/", methods=["GET"])
def list_catalog():
    Session = current_app.config["DB_SESSION"]
    session = Session()

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
    return jsonify([_catalog_to_dict(e) for e in entries])


@catalog_bp.route("/<int:catalog_id>", methods=["GET"])
def get_catalog_entry(catalog_id):
    Session = current_app.config["DB_SESSION"]
    session = Session()

    entry = session.get(Catalog, catalog_id)
    if entry is None:
        return jsonify({"error": "Catalog entry not found"}), 404

    return jsonify(_catalog_to_dict(entry))


def _run_sync_in_background(cfg: dict) -> None:
    global _sync_running, _last_result, _last_run_at, _sync_progress

    def _progress(current, total):
        global _sync_progress
        with _sync_lock:
            _sync_progress = {"current": current, "total": total}

    try:
        result = run_sync(cfg, progress_callback=_progress)
    except Exception as exc:
        logger.exception("My Movies sync (triggered via API) failed")
        result = {"error": str(exc)}
    finally:
        with _sync_lock:
            _last_result = result
            _last_run_at = datetime.now(timezone.utc).isoformat()
            _sync_running = False
            _sync_progress = None


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

    rows = []

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
            "disc_temp_name": disc.temp_name,
            "disc_ripped_at": disc.ripped_at.isoformat() if disc.ripped_at else None,
            "disc_rip_quality": disc.rip_quality,
        }

    def _catalog_fields(catalog):
        return {
            "catalog_id": catalog.id,
            "catalog_title": catalog.title,
            "catalog_year": catalog.year,
            "catalog_imdb_id": catalog.imdb_id,
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
        if id_status == "identified":
            q = q.where(Disc.temp_name.isnot(None))
        elif id_status == "unidentified":
            q = q.where(Disc.temp_name.is_(None))
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
        rip_status in (None, "unripped") and
        id_status in (None, "unidentified") and
        mm_status in (None, "matched")
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
                "disc_ripped_at": None, "disc_rip_quality": None,
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
        if id_status == "identified":
            q = q.where(Disc.temp_name.isnot(None))
        elif id_status == "unidentified":
            q = q.where(Disc.temp_name.is_(None))
        for disc in session.scalars(_search_matches_disc(q).order_by(Disc.temp_name)):
            rows.append({
                "row_type": "unmatched_rip",
                "catalog_id": None, "catalog_title": None,
                "catalog_year": None, "catalog_imdb_id": None,
                **_disc_fields(disc),
            })

    return jsonify(rows)


@catalog_bp.route("/sync", methods=["POST"])
def trigger_sync():
    cfg = current_app.config["DISCRIPPER_CFG"]

    global _sync_running
    with _sync_lock:
        if _sync_running:
            return jsonify({"status": "already_running"})
        _sync_running = True

    thread = threading.Thread(target=_run_sync_in_background, args=(cfg,), daemon=True)
    thread.start()

    return jsonify({"status": "started"})


@catalog_bp.route("/sync/status", methods=["GET"])
def sync_status():
    with _sync_lock:
        return jsonify({
            "running": _sync_running,
            "last_result": _last_result,
            "last_run_at": _last_run_at,
            "progress": _sync_progress if _sync_running else None,
        })
