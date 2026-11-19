"""
Bulk ISO import API.

GET  /api/import/preview  -- per-file status, job state, duplicate warnings
POST /api/import/start    -- start background import job (atomic, returns immediately)
POST /api/import/stop     -- signal running job to stop
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, current_app, request
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import joinedload, sessionmaker

from common.catalog_dups import load_dup_roles
from common.config import get_db_url
from common.import_helpers import (
    build_catalog_lookup,
    find_catalog_match_from_lookup,
    import_one_iso,
    make_working_title,
    parse_iso_filename,
)
from common.models import Catalog, Disc, DiscStatus, DiscType, EncodeJob, JobStatus, Setting, naive_utcnow
from ripper_service.region_patcher import MIN_ISO_SIZE

import_bp = Blueprint("import_bp", __name__)
logger = logging.getLogger(__name__)

_STATUS_KEY = "import_job_status"
_HEARTBEAT_KEY = "import_job_heartbeat"
_TOTALS_KEY = "import_job_totals"
_COMMAND_KEY = "import_job_command"
_ERROR_KEY = "import_job_error"
_LAST_RUN_KEY = "import_job_last_run"

_HEARTBEAT_STALE_SECONDS = 120


def _get_setting(session, key: str) -> str:
    row = session.get(Setting, key)
    return row.value if row else ""


def _set_setting(session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


def _effective_status(raw_status: str, heartbeat: str) -> str:
    """Return 'interrupted' if status is 'running' but heartbeat is stale."""
    if raw_status != "running":
        return raw_status
    if not heartbeat:
        return "interrupted"
    try:
        hb_dt = datetime.fromisoformat(heartbeat)
        age = (naive_utcnow() - hb_dt).total_seconds()
        if age > _HEARTBEAT_STALE_SECONDS:
            return "interrupted"
    except ValueError:
        return "interrupted"
    return "running"


def _legacy_dir(cfg) -> Path:
    return Path(cfg["storage"]["datastore_root"]) / "dvd_store" / "legacy"


def _preflight_check(datastore_root: str) -> str | None:
    """
    Verify that dvd_store/legacy/ and dvd_store/raw/ are both writable.
    Returns an error message string on failure, None on success.
    """
    errors = []
    for rel in ["dvd_store/legacy", "dvd_store/raw"]:
        d = Path(datastore_root) / rel
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(f"{rel}: cannot create ({exc.strerror})")
            continue
        if not os.access(str(d), os.W_OK):
            errors.append(f"{rel}: not writable by this user")
    return "; ".join(errors) if errors else None


@import_bp.route("/preview", methods=["GET"])
def preview():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]
    legacy_dir = _legacy_dir(cfg)

    # Job state
    raw_status = _get_setting(session, _STATUS_KEY) or "idle"
    heartbeat = _get_setting(session, _HEARTBEAT_KEY)
    totals_raw = _get_setting(session, _TOTALS_KEY)
    job_status = _effective_status(raw_status, heartbeat)
    try:
        totals = json.loads(totals_raw) if totals_raw else {}
    except ValueError:
        totals = {}

    # Collect ISO files from legacy/
    legacy_isos = []
    if legacy_dir.exists():
        legacy_isos = sorted(legacy_dir.glob("*.iso"), key=lambda p: p.name.lower())
    legacy_filenames = {p.name for p in legacy_isos}

    # Build catalog lookup once per request
    catalog_lookup = build_catalog_lookup(session)

    # Get all imported disc records (by filename)
    imported_rows = session.execute(
        select(Disc.imported_filename, Disc.id, Disc.status, Disc.catalog_id, Disc.error_message)
        .where(Disc.imported == True, Disc.imported_filename.isnot(None))
    ).all()
    imported_by_filename: dict[str, dict] = {
        row[0]: {
            "disc_id": row[1], "status": row[2],
            "catalog_id": row[3], "error_message": row[4],
        }
        for row in imported_rows
    }

    # Error imported discs whose files are NOT in legacy/ (move succeeded but import failed later)
    extra_filenames = [
        fname for fname, info in imported_by_filename.items()
        if info["status"] == DiscStatus.error and fname not in legacy_filenames
    ]

    all_filenames = [p.name for p in legacy_isos] + extra_filenames

    # Existing temp_names in DB for collision detection
    existing_temp_names_rows = session.execute(
        select(Disc.temp_name, Disc.imported_filename)
        .where(Disc.type == DiscType.dvd, Disc.temp_name.isnot(None))
    ).all()
    existing_temp_names: dict[str, str | None] = {
        (row[0] or "").lower(): row[1]
        for row in existing_temp_names_rows
    }

    # Pre-compute working titles and intra-batch collision groups
    file_wts: dict[str, str] = {}
    wt_to_filenames: dict[str, list[str]] = {}

    for filename in all_filenames:
        title_part, year = parse_iso_filename(filename)
        wt = make_working_title(title_part, year)
        file_wts[filename] = wt
        wt_lower = wt.lower()
        wt_to_filenames.setdefault(wt_lower, []).append(filename)

    files = []
    for filename in all_filenames:
        wt = file_wts[filename]
        wt_lower = wt.lower()
        title_part, year = parse_iso_filename(filename)
        catalog_match, catalog_id, catalog_title = find_catalog_match_from_lookup(
            catalog_lookup, title_part, year
        )

        # Size check
        try:
            size = (legacy_dir / filename).stat().st_size
            is_small = size < MIN_ISO_SIZE
            size_mb = size // (1024 * 1024)
        except FileNotFoundError:
            is_small = False
            size_mb = None

        # Status
        db_info = imported_by_filename.get(filename)
        if is_small:
            status = "skipped"
            reason = f"too small ({size_mb} MB)" if size_mb is not None else "too small"
        elif db_info and db_info["status"] != DiscStatus.error:
            status = "already_imported"
            reason = None
        elif db_info and db_info["status"] == DiscStatus.error:
            status = "error_retry"
            reason = db_info.get("error_message") or "failed on previous attempt"
        else:
            status = "pending"
            reason = None

        # Duplicate/collision warning
        same_title_files = wt_to_filenames.get(wt_lower, [])
        duplicate_warning = None
        if len(same_title_files) > 1:
            others = [f for f in same_title_files if f != filename]
            duplicate_warning = f"Title collision with {others[0]}"
        elif wt_lower in existing_temp_names:
            existing_fname = existing_temp_names[wt_lower]
            if existing_fname != filename:
                duplicate_warning = "Title already used by an existing disc"

        files.append({
            "filename": filename,
            "working_title": wt,
            "catalog_match": catalog_match,
            "catalog_id": catalog_id,
            "catalog_title": catalog_title,
            "status": status,
            "reason": reason,
            "duplicate_warning": duplicate_warning,
            "in_raw": filename in extra_filenames,
        })

    job_error = _get_setting(session, _ERROR_KEY) or None

    last_run_raw = _get_setting(session, _LAST_RUN_KEY)
    try:
        last_run = json.loads(last_run_raw) if last_run_raw else None
    except ValueError:
        last_run = None

    # If job is currently interrupted (stale heartbeat), annotate the last_run
    # outcome so the UI can show it without waiting for a new start to persist it.
    if last_run and job_status == "interrupted" and last_run.get("outcome") is None:
        last_run = dict(last_run, outcome="interrupted")

    return jsonify({
        "job_status": job_status,
        "totals": totals,
        "files": files,
        "job_error": job_error,
        "last_run": last_run,
    })


@import_bp.route("/discs", methods=["GET"])
def imported_discs():
    """
    List discs that entered through bulk import (imported=True), newest first.
    Query params: filter (all|needs_id|encoding|complete|error), page, per_page.
    """
    Session = current_app.config["DB_SESSION"]
    session = Session()

    disc_filter = request.args.get("filter", "all")
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(200, max(1, int(request.args.get("per_page", 50))))
    except (ValueError, TypeError):
        page, per_page = 1, 50

    discs = session.scalars(
        select(Disc)
        .options(joinedload(Disc.catalog))
        .where(Disc.imported == True)
        .order_by(Disc.created_at.desc())
    ).all()

    disc_ids = [d.id for d in discs]

    # Encode job counts per disc in one query
    encode_by_disc: dict[int, dict[str, int]] = {}
    if disc_ids:
        for disc_id, status, cnt in session.execute(
            select(EncodeJob.disc_id, EncodeJob.status, func.count(EncodeJob.id))
            .where(EncodeJob.disc_id.in_(disc_ids))
            .group_by(EncodeJob.disc_id, EncodeJob.status)
        ).all():
            key = status.value if hasattr(status, "value") else str(status)
            encode_by_disc.setdefault(disc_id, {})[key] = cnt

    def _enc(disc_id):
        jobs = encode_by_disc.get(disc_id, {})
        total_j = sum(jobs.values())
        return {
            "total": total_j,
            "done": jobs.get("done", 0),
            "error": jobs.get("error", 0),
            "active": jobs.get("queued", 0) + jobs.get("running", 0),
        }

    def _disc_filter_key(d, enc):
        if d.status == DiscStatus.error or enc["error"] > 0:
            return "error"
        if d.catalog_id is None:
            return "needs_id"
        if enc["active"] > 0:
            return "encoding"
        if enc["done"] > 0 and enc["total"] > 0 and enc["active"] == 0 and enc["error"] == 0:
            return "complete"
        return "other"

    rows = []
    counts = {"all": 0, "needs_id": 0, "encoding": 0, "complete": 0, "error": 0}
    for d in discs:
        enc = _enc(d.id)
        fk = _disc_filter_key(d, enc)
        counts["all"] += 1
        if fk in counts:
            counts[fk] += 1
        rows.append((d, enc, fk))

    if disc_filter != "all":
        rows = [(d, enc, fk) for d, enc, fk in rows if fk == disc_filter]

    total_filtered = len(rows)
    offset = (page - 1) * per_page
    page_rows = rows[offset: offset + per_page]

    dup_roles = load_dup_roles(session)
    result = []
    for d, enc, _fk in page_rows:
        dr = dup_roles.get(d.catalog_id) if d.catalog_id else None
        result.append({
            "id": d.id,
            "imported_filename": d.imported_filename,
            "temp_name": d.temp_name,
            "catalog_id": d.catalog_id,
            "catalog_title": d.catalog.title if d.catalog else None,
            "catalog_collection_number": d.catalog.collection_number if d.catalog else None,
            "catalog_edition": d.catalog.edition if d.catalog else None,
            "catalog_match_source": d.catalog_match_source,
            "catalog_duplicate_role": dr.role if dr and dr.role != "sole" else None,
            "disc_status": d.status.value if d.status else None,
            "error_message": d.error_message,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "encode": enc,
        })

    return jsonify({
        "discs": result,
        "counts": counts,
        "total": total_filtered,
        "page": page,
        "per_page": per_page,
        "has_more": offset + per_page < total_filtered,
    })


@import_bp.route("/start", methods=["POST"])
def start_import():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]
    legacy_dir = _legacy_dir(cfg)
    datastore_root = cfg["storage"]["datastore_root"]
    db_url = get_db_url(cfg)

    # Atomic start: SELECT FOR UPDATE prevents two gunicorn workers racing
    status_row = session.scalar(
        select(Setting).where(Setting.key == _STATUS_KEY).with_for_update()
    )
    current_status = status_row.value if status_row else "idle"

    if current_status == "running":
        heartbeat_row = session.get(Setting, _HEARTBEAT_KEY)
        heartbeat_val = heartbeat_row.value if heartbeat_row else ""
        if _effective_status("running", heartbeat_val) == "running":
            return jsonify({"error": "already running"}), 409
        # Stale heartbeat: record interrupted outcome on the previous last_run
        old_totals_raw = _get_setting(session, _TOTALS_KEY)
        try:
            old_totals = json.loads(old_totals_raw) if old_totals_raw else {}
        except ValueError:
            old_totals = {}
        old_lr_raw = _get_setting(session, _LAST_RUN_KEY)
        try:
            old_lr = json.loads(old_lr_raw) if old_lr_raw else {}
        except ValueError:
            old_lr = {}
        if old_lr.get("outcome") is None:
            old_lr.update({
                "finished_at": naive_utcnow().isoformat(),
                "outcome": "interrupted",
                "total": old_totals.get("total", 0),
                "imported": old_totals.get("imported", 0),
                "skipped": old_totals.get("skipped", 0),
                "already_imported": old_totals.get("already_imported", 0),
            })
            _set_setting(session, _LAST_RUN_KEY, json.dumps(old_lr))

    # Count all files to process
    legacy_isos = sorted(legacy_dir.glob("*.iso")) if legacy_dir.exists() else []
    legacy_filenames = {p.name for p in legacy_isos}

    imported_rows = session.execute(
        select(Disc.imported_filename, Disc.status)
        .where(Disc.imported == True, Disc.imported_filename.isnot(None))
    ).all()
    imported_by_fname = {row[0]: row[1] for row in imported_rows}

    extra_filenames = [
        fname for fname, st in imported_by_fname.items()
        if st == DiscStatus.error and fname not in legacy_filenames
    ]
    total = len(legacy_isos) + len(extra_filenames)

    # Preflight: verify write access before committing to "running"
    preflight_err = _preflight_check(datastore_root)
    if preflight_err:
        return jsonify({"error": preflight_err}), 400

    now = naive_utcnow().isoformat()
    _set_setting(session, _STATUS_KEY, "running")
    _set_setting(session, _COMMAND_KEY, "")
    _set_setting(session, _HEARTBEAT_KEY, now)
    _set_setting(session, _ERROR_KEY, "")
    _set_setting(session, _TOTALS_KEY, json.dumps({
        "total": total, "processed": 0, "imported": 0, "skipped": 0, "already_imported": 0,
    }))
    _set_setting(session, _LAST_RUN_KEY, json.dumps({
        "started_at": now,
        "finished_at": None,
        "outcome": None,
        "total": total,
        "imported": 0,
        "skipped": 0,
        "already_imported": 0,
    }))
    session.commit()

    thread = threading.Thread(
        target=_run_import_job,
        args=(db_url, datastore_root),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "started", "total": total})


@import_bp.route("/stop", methods=["POST"])
def stop_import():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    _set_setting(session, _COMMAND_KEY, "stop")
    session.commit()
    return jsonify({"status": "stopping"})


def _set_job_settings(Session, **settings) -> None:
    with Session() as session:
        for key, value in settings.items():
            _set_setting(session, key, value)
        session.commit()


def _finish_last_run(Session, outcome: str, totals: dict) -> None:
    """Persist the finished-state of the last run to _LAST_RUN_KEY."""
    with Session() as session:
        lr_raw = _get_setting(session, _LAST_RUN_KEY)
        try:
            lr = json.loads(lr_raw) if lr_raw else {}
        except ValueError:
            lr = {}
        lr.update({
            "finished_at": naive_utcnow().isoformat(),
            "outcome": outcome,
            "total": totals.get("total", 0),
            "imported": totals.get("imported", 0),
            "skipped": totals.get("skipped", 0),
            "already_imported": totals.get("already_imported", 0),
        })
        _set_setting(session, _LAST_RUN_KEY, json.dumps(lr))
        session.commit()


def _run_import_job(db_url: str, datastore_root: str) -> None:
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    legacy_dir = Path(datastore_root) / "dvd_store" / "legacy"

    try:
        # Preflight: fail fast rather than erroring on every file
        preflight_err = _preflight_check(datastore_root)
        if preflight_err:
            _set_job_settings(Session, **{_STATUS_KEY: "error", _ERROR_KEY: preflight_err})
            return

        # Build catalog lookup once at job start
        with Session() as session:
            catalog_lookup = build_catalog_lookup(session)

        # Collect files
        legacy_isos = sorted(legacy_dir.glob("*.iso"), key=lambda p: p.name.lower()) if legacy_dir.exists() else []
        legacy_filenames = {p.name for p in legacy_isos}

        with Session() as session:
            error_rows = session.execute(
                select(Disc.imported_filename, Disc.status)
                .where(
                    Disc.imported == True,
                    Disc.status == DiscStatus.error,
                    Disc.imported_filename.isnot(None),
                )
            ).all()
        extra_filenames = [row[0] for row in error_rows if row[0] not in legacy_filenames]
        all_filenames = [p.name for p in legacy_isos] + extra_filenames
        total = len(all_filenames)

        imported = skipped = already_imported = 0

        for i, filename in enumerate(all_filenames):
            # Heartbeat + stop check before each file
            with Session() as session:
                cmd = _get_setting(session, _COMMAND_KEY)
                _set_setting(session, _HEARTBEAT_KEY, naive_utcnow().isoformat())
                session.commit()

            if cmd == "stop":
                _finish_last_run(Session, "stopped", {
                    "total": total, "imported": imported,
                    "skipped": skipped, "already_imported": already_imported,
                })
                _set_job_settings(Session, **{_STATUS_KEY: "idle", _COMMAND_KEY: ""})
                return

            with Session() as session:
                result = import_one_iso(session, datastore_root, filename,
                                        dry_run=False, catalog_lookup=catalog_lookup)

            if result["status"] == "imported":
                imported += 1
            elif result["status"] == "already_imported":
                already_imported += 1
            else:
                skipped += 1

            with Session() as session:
                _set_setting(session, _TOTALS_KEY, json.dumps({
                    "total": total,
                    "processed": i + 1,
                    "imported": imported,
                    "skipped": skipped,
                    "already_imported": already_imported,
                }))
                _set_setting(session, _HEARTBEAT_KEY, naive_utcnow().isoformat())
                session.commit()

        final_totals = {
            "total": total, "processed": total,
            "imported": imported, "skipped": skipped, "already_imported": already_imported,
        }
        _finish_last_run(Session, "done", final_totals)
        _set_job_settings(Session, **{
            _STATUS_KEY: "done",
            _TOTALS_KEY: json.dumps(final_totals),
        })

    except Exception as exc:
        logger.exception("Import job crashed")
        # Read current totals so last_run reflects progress so far
        try:
            with Session() as session:
                partial_raw = _get_setting(session, _TOTALS_KEY)
                partial = json.loads(partial_raw) if partial_raw else {}
        except Exception:
            partial = {}
        _finish_last_run(Session, "error", partial)
        _set_job_settings(Session, **{
            _STATUS_KEY: "error",
            _ERROR_KEY: str(exc)[:500],
        })
