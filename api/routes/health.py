"""
Health endpoints — library statistics dashboard and drive health.
"""

from pathlib import Path

from flask import Blueprint, jsonify, current_app, request as flask_request
from sqlalchemy import exists, select, func, text

from common.models import (
    Catalog, CDTrack, Disc, DiscStatus, DiscType, EncodeJob, EncodeProfile,
    JobStatus, RipQuality,
)

health_bp = Blueprint("health", __name__)

# Statuses excluded from the identification queue — mirrors discs.py identification_queue().
_QUEUE_EXCLUDED = [DiscStatus.queued, DiscStatus.ripping, DiscStatus.building, DiscStatus.cancelled]


@health_bp.route("/", methods=["GET"])
def health():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    def count(model, *where):
        q = select(func.count()).select_from(model)
        if where:
            q = q.where(*where)
        return session.scalar(q) or 0

    # "Matched to ripped" = catalog entry has at least one disc with a usable ISO.
    # A matched failed disc (error/protected) doesn't count — it has no ISO and
    # the title still needs ripping.
    _USABLE_STATUSES = [DiscStatus.ripped, DiscStatus.encoding, DiscStatus.done]
    disc_exists = select(Disc.id).where(
        Disc.catalog_id == Catalog.id,
        Disc.status.in_(_USABLE_STATUSES),
    ).correlate(Catalog).exists()
    matched_count = session.scalar(
        select(func.count()).select_from(Catalog).where(disc_exists)
    ) or 0
    catalog_total = count(Catalog)
    last_sync_dt = session.scalar(select(func.max(Catalog.synced_at)))
    last_sync = last_sync_dt.isoformat() if last_sync_dt else None

    cfg = current_app.config["DISCRIPPER_CFG"]
    covers_dir = Path(cfg["storage"]["datastore_root"]) / "cd_store" / "covers"
    cover_ids: set[int] = set()
    if covers_dir.exists():
        for f in covers_dir.iterdir():
            if f.suffix == ".jpg" and f.stem.isdigit():
                cover_ids.add(int(f.stem))
    identified_cd_ids = set(session.scalars(
        select(Disc.id).where(Disc.type == DiscType.cd, Disc.album_title.isnot(None))
    ).all())
    cds_with_cover    = len(identified_cd_ids & cover_ids)
    cds_without_cover = len(identified_cd_ids - cover_ids)

    def _encode_counts(media_type):
        rows = session.execute(
            select(EncodeJob.status, func.count().label("cnt"))
            .join(EncodeProfile, EncodeJob.profile_id == EncodeProfile.id)
            .where(EncodeProfile.media_type == media_type)
            .group_by(EncodeJob.status)
        ).all()
        counts = {"queued": 0, "running": 0, "complete": 0, "error": 0}
        for row in rows:
            sv = row.status.value if hasattr(row.status, "value") else str(row.status)
            key = "complete" if sv == "done" else sv
            if key in counts:
                counts[key] += row.cnt
        return counts

    return jsonify({
        "status": "ok",
        "library": {
            "dvd_count": count(Disc, Disc.type == DiscType.dvd),
            "cd_count": count(Disc, Disc.type == DiscType.cd),
            "cd_track_count": count(CDTrack),
        },
        "my_movies": {
            "catalog_count": catalog_total,
            "matched_to_ripped": matched_count,
            "never_ripped": catalog_total - matched_count,
            "last_sync": last_sync,
        },
        "identification": {
            "dvds_matched": count(Disc, Disc.type == DiscType.dvd, Disc.catalog_id.isnot(None)),
            "dvds_unmatched": count(
                Disc,
                Disc.type == DiscType.dvd,
                Disc.catalog_id.is_(None),
                Disc.status.notin_(_QUEUE_EXCLUDED),
            ),
            "cds_identified": count(Disc, Disc.type == DiscType.cd, Disc.album_title.isnot(None)),
            "cds_unidentified": count(
                Disc,
                Disc.type == DiscType.cd,
                Disc.album_title.is_(None),
                Disc.status.notin_(_QUEUE_EXCLUDED),
            ),
            "cd_tracks_titled": count(CDTrack, CDTrack.title.isnot(None)),
            "cd_tracks_untitled": count(CDTrack, CDTrack.title.is_(None)),
        },
        "quality": {
            "discs_needing_rerip": count(Disc, Disc.needs_rerip.is_(True)),
            "dirty_rips": count(Disc, Disc.rip_quality == "dirty"),
            "imperfect_tracks": count(
                CDTrack,
                CDTrack.rip_quality.in_([RipQuality.imperfect, RipQuality.failed]),
            ),
        },
        "musicbrainz": {
            "cds_mb_found": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "found"),
            "cds_mb_not_found": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "not_found"),
            "cds_mb_pending": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "pending"),
            "cds_mb_error": count(Disc, Disc.type == DiscType.cd, Disc.mb_lookup_status == "error"),
        },
        "pipeline": {
            "currently_ripping": count(Disc, Disc.status == DiscStatus.ripping),
            "currently_building": count(Disc, Disc.status == DiscStatus.building),
            "currently_identifying": count(Disc, Disc.status == DiscStatus.identifying),
            "error_discs": count(Disc, Disc.status == DiscStatus.error),
            "cancelled_discs": count(Disc, Disc.status == DiscStatus.cancelled),
            "dvds_protected": count(Disc, Disc.type == DiscType.dvd, Disc.status == DiscStatus.protected),
        },
        "artwork": {
            "cds_with_cover":    cds_with_cover,
            "cds_without_cover": cds_without_cover,
        },
        "dvd_encodes": _encode_counts("dvd"),
        "cd_encodes": _encode_counts("cd"),
    })


def _disc_row(disc):
    return {
        "disc_id": disc.id,
        "type": disc.type.value if hasattr(disc.type, "value") else str(disc.type),
        "temp_name": disc.temp_name or f"Disc #{disc.id}",
        "disc_fingerprint": disc.disc_fingerprint,
        "ripped_at": disc.ripped_at.isoformat() if disc.ripped_at else None,
        "error_message": disc.error_message,
        "created_at": disc.created_at.isoformat() if disc.created_at else None,
    }


@health_bp.route("/mb-not-found", methods=["GET"])
def mb_not_found():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.mb_lookup_status == "not_found",
        ).order_by(Disc.ripped_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/mb-error", methods=["GET"])
def mb_error():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.mb_lookup_status == "error",
        ).order_by(Disc.ripped_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/pipeline-identifying", methods=["GET"])
def pipeline_identifying():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(Disc.status == DiscStatus.identifying)
        .order_by(Disc.ripped_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/pipeline-errors", methods=["GET"])
def pipeline_errors():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(Disc.status == DiscStatus.error)
        .order_by(Disc.created_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/dvds-protected", methods=["GET"])
def dvds_protected():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    discs = session.scalars(
        select(Disc).where(
            Disc.type == DiscType.dvd,
            Disc.status == DiscStatus.protected,
        ).order_by(Disc.created_at.desc())
    ).all()
    return jsonify([_disc_row(d) for d in discs])


@health_bp.route("/drive-health", methods=["GET"])
def drive_health():
    """
    Per-drive rip statistics and cross-drive retry analysis.
    ?env=prod|dev  defaults to prod.
    Cancelled jobs are excluded from all counts and rates.
    All outcome counts are sourced from rip_jobs.outcome (populated since
    migration 0036); pre-existing jobs without outcome are excluded from
    per-job stats but still count in total_attempts.
    """
    Session = current_app.config["DB_SESSION"]
    session = Session()

    env = flask_request.args.get("env", "prod")
    if env not in ("dev", "prod"):
        env = "prod"

    # ── Per-drive aggregates ───────────────────────────────────────────────────
    drive_rows = session.execute(text("""
        SELECT
            d.id               AS drive_id,
            d.label,
            d.device_path,
            COUNT(rj.id) FILTER (WHERE rj.outcome != 'cancelled'
                                    OR rj.outcome IS NULL)::int           AS total_attempts,
            COUNT(rj.id) FILTER (WHERE rj.outcome = 'clean')::int         AS clean_count,
            COUNT(rj.id) FILTER (WHERE rj.outcome = 'dirty')::int         AS dirty_count,
            COUNT(rj.id) FILTER (WHERE rj.outcome = 'failed')::int        AS failed_count,
            COUNT(rj.id) FILTER (WHERE rj.dvdbackup_outcome = 'timed_out')::int
                                                                           AS timed_out_count,
            AVG(rj.dvdbackup_seconds)
                FILTER (WHERE rj.outcome = 'clean'
                    AND rj.dvdbackup_outcome = 'clean'
                    AND rj.dvdbackup_seconds IS NOT NULL)
                AS avg_dvdbackup_secs,
            AVG(rj.makemkv_seconds)
                FILTER (WHERE rj.outcome = 'clean'
                    AND rj.makemkv_outcome = 'clean'
                    AND rj.makemkv_seconds IS NOT NULL)
                AS avg_makemkv_secs,
            AVG(rj.iso_size_bytes::float / 1048576.0 / rj.dvdbackup_seconds)
                FILTER (WHERE rj.outcome = 'clean'
                    AND rj.dvdbackup_outcome = 'clean'
                    AND rj.dvdbackup_seconds > 0
                    AND rj.iso_size_bytes IS NOT NULL)
                AS avg_dvdbackup_speed_mbps,
            AVG(rj.iso_size_bytes::float / 1048576.0 / rj.makemkv_seconds)
                FILTER (WHERE rj.outcome = 'clean'
                    AND rj.makemkv_outcome = 'clean'
                    AND rj.makemkv_seconds > 0
                    AND rj.iso_size_bytes IS NOT NULL)
                AS avg_makemkv_speed_mbps
        FROM drives d
        LEFT JOIN rip_jobs rj ON rj.drive_id = d.id
        WHERE d.env = :env AND d.active = TRUE
        GROUP BY d.id, d.label, d.device_path
        ORDER BY d.label
    """), {"env": env}).mappings().all()

    # ── Last 10 terminal outcomes per drive (most recent first) ───────────────
    last10_rows = session.execute(text("""
        WITH ranked AS (
            SELECT
                rj.drive_id,
                COALESCE(rj.outcome, 'unknown') AS outcome,
                ROW_NUMBER() OVER (
                    PARTITION BY rj.drive_id
                    ORDER BY COALESCE(rj.completed_at, rj.created_at) DESC
                ) AS rn
            FROM rip_jobs rj
            WHERE rj.outcome IN ('clean', 'dirty', 'failed')
              AND rj.drive_id IS NOT NULL
        )
        SELECT drive_id, outcome FROM ranked WHERE rn <= 10 ORDER BY drive_id, rn
    """)).mappings().all()
    last10_by_drive: dict[int, list[str]] = {}
    for r in last10_rows:
        last10_by_drive.setdefault(r["drive_id"], []).append(r["outcome"])

    # ── Failed-here-OK-elsewhere: discs with outcome dirty/failed on THIS drive
    #    then outcome clean on a different drive ──────────────────────────────
    rescue_rows = session.execute(text("""
        SELECT fail_job.drive_id AS drive_id,
               COUNT(DISTINCT fail_job.disc_id)::int AS rescued_count
        FROM rip_jobs fail_job
        JOIN rip_jobs success_job
            ON  success_job.disc_id  = fail_job.disc_id
            AND success_job.drive_id != fail_job.drive_id
            AND success_job.outcome  = 'clean'
            AND COALESCE(success_job.completed_at, success_job.created_at)
                > COALESCE(fail_job.completed_at,  fail_job.created_at)
        JOIN drives fail_drive ON fail_drive.id = fail_job.drive_id
        WHERE fail_job.outcome IN ('dirty', 'failed') AND fail_drive.env = :env
        GROUP BY fail_job.drive_id
    """), {"env": env}).mappings().all()
    rescued_by_drive = {r["drive_id"]: r["rescued_count"] for r in rescue_rows}

    # ── Cross-drive retry list (one row per disc, most-recent failure first) ───
    retry_rows = session.execute(text("""
        SELECT DISTINCT ON (fail_job.disc_id)
            fail_job.disc_id,
            COALESCE(di.temp_name, 'disc_' || fail_job.disc_id::text) AS working_title,
            fail_drive.id    AS failed_drive_id,
            fail_drive.label AS failed_drive_label,
            fail_job.completed_at                                      AS failed_at,
            success_drive.id    AS success_drive_id,
            success_drive.label AS success_drive_label,
            success_job.completed_at                                   AS success_at
        FROM rip_jobs fail_job
        JOIN rip_jobs success_job
            ON  success_job.disc_id  = fail_job.disc_id
            AND success_job.drive_id != fail_job.drive_id
            AND success_job.outcome  = 'clean'
            AND COALESCE(success_job.completed_at, success_job.created_at)
                > COALESCE(fail_job.completed_at,  fail_job.created_at)
        JOIN drives fail_drive    ON fail_drive.id    = fail_job.drive_id
        JOIN drives success_drive ON success_drive.id = success_job.drive_id
        JOIN discs di             ON di.id            = fail_job.disc_id
        WHERE fail_job.outcome IN ('dirty', 'failed')
          AND (fail_drive.env = :env OR success_drive.env = :env)
        ORDER BY fail_job.disc_id, fail_job.completed_at DESC NULLS LAST
    """), {"env": env}).mappings().all()

    # ── Build drive objects ───────────────────────────────────────────────────
    def _float(v):
        return float(v) if v is not None else None

    drives = []
    for r in drive_rows:
        total  = r["total_attempts"] or 0
        clean  = r["clean_count"]  or 0
        dirty  = r["dirty_count"]  or 0
        failed = r["failed_count"] or 0
        good   = clean + dirty
        success_rate = round(good / total, 3) if total > 0 else None

        drives.append({
            "drive_id": r["drive_id"],
            "label": r["label"],
            "device_path": r["device_path"],
            "total_attempts": total,
            "clean": clean,
            "dirty": dirty,
            "failed": failed,
            "timed_out": r["timed_out_count"] or 0,
            "failed_here_ok_elsewhere": rescued_by_drive.get(r["drive_id"], 0),
            "success_rate": success_rate,
            "avg_clean_dvdbackup_seconds": _float(r["avg_dvdbackup_secs"]),
            "avg_clean_makemkv_seconds": _float(r["avg_makemkv_secs"]),
            "avg_read_speed_dvdbackup_mbps": _float(r["avg_dvdbackup_speed_mbps"]),
            "avg_read_speed_makemkv_mbps": _float(r["avg_makemkv_speed_mbps"]),
            "last_10_outcomes": last10_by_drive.get(r["drive_id"], []),
            "highlight": False,
        })

    # Highlight drives whose success rate is ≥15pp below fleet average.
    # Requires ≥5 attempts and at least 2 comparable drives.
    eligible = [d for d in drives if d["total_attempts"] >= 5]
    if len(eligible) >= 2:
        fleet_total = sum(d["total_attempts"] for d in eligible)
        fleet_good  = sum(d["clean"] + d["dirty"] for d in eligible)
        fleet_avg   = fleet_good / fleet_total if fleet_total > 0 else 1.0
        for d in drives:
            if d["total_attempts"] >= 5 and d["success_rate"] is not None:
                d["highlight"] = d["success_rate"] < fleet_avg - 0.15

    cross_drive_retries = [
        {
            "disc_id":             r["disc_id"],
            "working_title":       r["working_title"],
            "failed_drive_id":     r["failed_drive_id"],
            "failed_drive_label":  r["failed_drive_label"],
            "failed_at":           r["failed_at"].isoformat() if r["failed_at"] else None,
            "success_drive_id":    r["success_drive_id"],
            "success_drive_label": r["success_drive_label"],
            "success_at":          r["success_at"].isoformat() if r["success_at"] else None,
        }
        for r in retry_rows
    ]

    return jsonify({
        "env": env,
        "drives": drives,
        "cross_drive_retries": cross_drive_retries,
    })
