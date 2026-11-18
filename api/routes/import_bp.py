"""
Bulk ISO import API.

GET  /api/import/preview  -- per-file status, job state, duplicate warnings
POST /api/import/start    -- start background import job (atomic, returns immediately)
POST /api/import/stop     -- signal running job to stop
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, current_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from common.config import get_db_url
from common.import_helpers import (
    build_catalog_lookup,
    find_catalog_match_from_lookup,
    import_one_iso,
    make_working_title,
    parse_iso_filename,
)
from common.models import Disc, DiscStatus, DiscType, Setting, naive_utcnow
from ripper_service.region_patcher import MIN_ISO_SIZE

import_bp = Blueprint("import_bp", __name__)
logger = logging.getLogger(__name__)

_STATUS_KEY = "import_job_status"
_HEARTBEAT_KEY = "import_job_heartbeat"
_TOTALS_KEY = "import_job_totals"
_COMMAND_KEY = "import_job_command"
_ERROR_KEY = "import_job_error"

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
        select(Disc.imported_filename, Disc.id, Disc.status, Disc.catalog_id)
        .where(Disc.imported == True, Disc.imported_filename.isnot(None))
    ).all()
    imported_by_filename: dict[str, dict] = {
        row[0]: {"disc_id": row[1], "status": row[2], "catalog_id": row[3]}
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
        catalog_match, catalog_id, catalog_title = find_catalog_match_from_lookup(
            catalog_lookup, wt
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
            reason = "failed on previous attempt"
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

    return jsonify({
        "job_status": job_status,
        "totals": totals,
        "files": files,
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
        # Stale heartbeat: allow restart

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

    _set_setting(session, _STATUS_KEY, "running")
    _set_setting(session, _COMMAND_KEY, "")
    _set_setting(session, _HEARTBEAT_KEY, naive_utcnow().isoformat())
    _set_setting(session, _ERROR_KEY, "")
    _set_setting(session, _TOTALS_KEY, json.dumps({
        "total": total, "processed": 0, "imported": 0, "skipped": 0, "already_imported": 0,
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


def _run_import_job(db_url: str, datastore_root: str) -> None:
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    legacy_dir = Path(datastore_root) / "dvd_store" / "legacy"

    try:
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

        _set_job_settings(Session, **{
            _STATUS_KEY: "done",
            _TOTALS_KEY: json.dumps({
                "total": total,
                "processed": total,
                "imported": imported,
                "skipped": skipped,
                "already_imported": already_imported,
            }),
        })

    except Exception as exc:
        logger.exception("Import job crashed")
        _set_job_settings(Session, **{
            _STATUS_KEY: "error",
            _ERROR_KEY: str(exc)[:500],
        })
