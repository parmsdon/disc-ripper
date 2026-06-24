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
from sqlalchemy import select, func

from common.models import Catalog
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


@catalog_bp.route("/", methods=["GET"])
def list_catalog():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    query = select(Catalog)

    search = request.args.get("search")
    if search:
        query = query.where(Catalog.title.ilike(f"%{search}%"))

    entries = session.scalars(query.order_by(Catalog.title)).all()
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
    global _sync_running, _last_result, _last_run_at
    try:
        result = run_sync(cfg)
    except Exception as exc:
        logger.exception("My Movies sync (triggered via API) failed")
        result = {"error": str(exc)}
    finally:
        with _sync_lock:
            _last_result = result
            _last_run_at = datetime.now(timezone.utc).isoformat()
            _sync_running = False


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
        })
