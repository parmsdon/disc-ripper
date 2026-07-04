"""
Audit API — DB/filesystem consistency checks.
"""

from pathlib import Path

from flask import Blueprint, jsonify, current_app
from sqlalchemy import select, func

from common.encode_queue import create_encode_jobs
from common.models import (
    Disc, DiscType, DiscStatus, Drive, RipJob, EncodeJob, EncodeProfile, JobStatus,
)

audit_bp = Blueprint("audit", __name__)


def _disc_title(disc):
    return disc.temp_name or disc.album_title or f"Disc #{disc.id}"


@audit_bp.route("/create-missing-encode-jobs", methods=["POST"])
def create_missing_encode_jobs():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    dvd_created = 0
    for disc in session.scalars(
        select(Disc).where(
            Disc.type == DiscType.dvd,
            Disc.status.in_([DiscStatus.ripped, DiscStatus.done]),
            Disc.raw_path.isnot(None),
        )
    ).all():
        dvd_created += create_encode_jobs(session, disc.id, "dvd")

    cd_created = 0
    for disc in session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.status.in_([DiscStatus.ripped, DiscStatus.done]),
            Disc.raw_path.isnot(None),
        )
    ).all():
        cd_created += create_encode_jobs(session, disc.id, "cd")

    return jsonify({"dvd_jobs_created": dvd_created, "cd_jobs_created": cd_created})


@audit_bp.route("/", methods=["GET"])
def run_audit():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]

    store_root = Path(cfg["storage"]["datastore_root"])
    dvd_raw = store_root / "dvd_store" / "raw"
    cd_raw = store_root / "cd_store" / "raw"

    # ── DVD: duplicate fingerprints ──────────────────────────────────────────
    dup_fp_rows = session.execute(
        select(Disc.disc_fingerprint, func.count(Disc.id).label("cnt"))
        .where(Disc.type == DiscType.dvd, Disc.disc_fingerprint.isnot(None))
        .group_by(Disc.disc_fingerprint)
        .having(func.count(Disc.id) > 1)
    ).all()
    duplicate_dvd_discs = []
    for row in dup_fp_rows:
        discs = session.scalars(
            select(Disc).where(
                Disc.disc_fingerprint == row.disc_fingerprint,
                Disc.type == DiscType.dvd,
            )
        ).all()
        duplicate_dvd_discs.append({
            "fingerprint": row.disc_fingerprint,
            "disc_ids": [d.id for d in discs],
            "titles": [_disc_title(d) for d in discs],
            "statuses": [d.status for d in discs],
        })

    # ── DVD: missing ISO files ───────────────────────────────────────────────
    missing_iso_files = []
    for disc in session.scalars(
        select(Disc).where(Disc.type == DiscType.dvd, Disc.raw_path.isnot(None))
    ).all():
        disc_dir = store_root / disc.raw_path
        if not disc_dir.exists() or not list(disc_dir.glob("*.iso")):
            missing_iso_files.append({
                "disc_id": disc.id,
                "title": _disc_title(disc),
                "status": disc.status,
                "raw_path": disc.raw_path,
            })

    # ── DVD: orphaned raw directories ────────────────────────────────────────
    all_dvd_ids = set(
        session.scalars(select(Disc.id).where(Disc.type == DiscType.dvd)).all()
    )
    orphaned_iso_dirs = []
    if dvd_raw.exists():
        for entry in sorted(dvd_raw.iterdir()):
            if entry.is_dir() and entry.name.isdigit():
                if int(entry.name) not in all_dvd_ids:
                    files = sorted(f.name for f in entry.iterdir() if f.is_file())
                    orphaned_iso_dirs.append({
                        "dir_name": entry.name,
                        "path": str(entry.relative_to(store_root)),
                        "files": files,
                    })

    # ── DVD: terminal status with null raw_path ──────────────────────────────
    null_raw_path = [
        {"disc_id": d.id, "title": _disc_title(d), "status": d.status}
        for d in session.scalars(
            select(Disc).where(
                Disc.type == DiscType.dvd,
                Disc.status.in_([DiscStatus.ripped, DiscStatus.done]),
                Disc.raw_path.is_(None),
            )
        ).all()
    ]

    # ── DVD: stale drive associations ────────────────────────────────────────
    stale_dvd_associations = []
    for disc in session.scalars(
        select(Disc).where(
            Disc.type == DiscType.dvd,
            Disc.status.in_([DiscStatus.ripped, DiscStatus.done, DiscStatus.error]),
            Disc.drive_id.isnot(None),
        )
    ).all():
        stale_dvd_associations.append({
            "disc_id": disc.id,
            "title": _disc_title(disc),
            "status": disc.status,
            "drive_id": disc.drive_id,
            "drive_label": disc.drive.label if disc.drive else None,
        })

    # ── CD: duplicate fingerprints ───────────────────────────────────────────
    dup_cd_rows = session.execute(
        select(Disc.disc_fingerprint, func.count(Disc.id).label("cnt"))
        .where(Disc.type == DiscType.cd, Disc.disc_fingerprint.isnot(None))
        .group_by(Disc.disc_fingerprint)
        .having(func.count(Disc.id) > 1)
    ).all()
    duplicate_cd_discs = []
    for row in dup_cd_rows:
        discs = session.scalars(
            select(Disc).where(
                Disc.disc_fingerprint == row.disc_fingerprint,
                Disc.type == DiscType.cd,
            )
        ).all()
        duplicate_cd_discs.append({
            "fingerprint": row.disc_fingerprint,
            "disc_ids": [d.id for d in discs],
            "titles": [_disc_title(d) for d in discs],
            "statuses": [d.status for d in discs],
        })

    # ── CD: missing WAV files ────────────────────────────────────────────────
    missing_wav_files = []
    for disc in session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.raw_path.isnot(None),
            Disc.needs_rerip == False,
            (Disc.rip_quality.is_(None) | (Disc.rip_quality != "dirty")),
        )
    ).all():
        disc_dir = store_root / disc.raw_path
        for track in disc.tracks:
            if track.wav_filename is None:
                continue
            if not (disc_dir / track.wav_filename).exists():
                missing_wav_files.append({
                    "disc_id": disc.id,
                    "title": _disc_title(disc),
                    "track_number": track.track_number,
                    "wav_filename": track.wav_filename,
                    "raw_path": disc.raw_path,
                })

    # ── CD: orphaned raw directories ─────────────────────────────────────────
    all_cd_ids = set(
        session.scalars(select(Disc.id).where(Disc.type == DiscType.cd)).all()
    )
    orphaned_wav_dirs = []
    if cd_raw.exists():
        for entry in sorted(cd_raw.iterdir()):
            if entry.is_dir() and entry.name.isdigit():
                if int(entry.name) not in all_cd_ids:
                    orphaned_wav_dirs.append({
                        "dir_name": entry.name,
                        "path": str(entry.relative_to(store_root)),
                    })

    # ── CD: done/ripped tracks missing wav_filename ──────────────────────────
    tracks_missing_wav_filename = []
    for disc in session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.status.in_([DiscStatus.ripped, DiscStatus.done]),
            Disc.needs_rerip == False,
            (Disc.rip_quality.is_(None) | (Disc.rip_quality != "dirty")),
        )
    ).all():
        missing = [t for t in disc.tracks if t.wav_filename is None]
        if missing:
            tracks_missing_wav_filename.append({
                "disc_id": disc.id,
                "title": _disc_title(disc),
                "status": disc.status,
                "track_count": len(disc.tracks),
                "missing_count": len(missing),
            })

    # ── DVD: missing encode jobs ─────────────────────────────────────────────
    dvd_profiles = session.scalars(
        select(EncodeProfile).where(
            EncodeProfile.media_type == "dvd",
            EncodeProfile.enabled == True,
        )
    ).all()

    missing_dvd_encode_jobs = []
    for disc in session.scalars(
        select(Disc).where(
            Disc.type == DiscType.dvd,
            Disc.status.in_([DiscStatus.ripped, DiscStatus.done]),
            Disc.raw_path.isnot(None),
        )
    ).all():
        existing_profile_ids = {
            job.profile_id
            for job in session.scalars(
                select(EncodeJob).where(
                    EncodeJob.disc_id == disc.id,
                    EncodeJob.status != JobStatus.error,
                )
            ).all()
        }
        missing = [
            {"id": p.id, "name": p.name}
            for p in dvd_profiles
            if p.id not in existing_profile_ids
        ]
        if missing:
            missing_dvd_encode_jobs.append({
                "disc_id": disc.id,
                "temp_name": _disc_title(disc),
                "missing_profiles": missing,
            })

    # ── CD: missing encode jobs ──────────────────────────────────────────────
    cd_profiles = session.scalars(
        select(EncodeProfile).where(
            EncodeProfile.media_type == "cd",
            EncodeProfile.enabled == True,
        )
    ).all()

    missing_cd_encode_jobs = []
    for disc in session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.status.in_([DiscStatus.ripped, DiscStatus.done]),
            Disc.raw_path.isnot(None),
        )
    ).all():
        trackable = [t for t in disc.tracks if t.wav_filename is not None]
        if not trackable:
            continue

        existing_combos = {
            (job.track_id, job.profile_id)
            for job in session.scalars(
                select(EncodeJob).where(
                    EncodeJob.disc_id == disc.id,
                    EncodeJob.status != JobStatus.error,
                )
            ).all()
        }

        missing_profile_ids = set()
        affected_track_ids = set()
        for track in trackable:
            for profile in cd_profiles:
                if (track.id, profile.id) not in existing_combos:
                    missing_profile_ids.add(profile.id)
                    affected_track_ids.add(track.id)

        if missing_profile_ids:
            missing_cd_encode_jobs.append({
                "disc_id": disc.id,
                "temp_name": _disc_title(disc),
                "missing_profiles": [
                    {"id": p.id, "name": p.name}
                    for p in cd_profiles
                    if p.id in missing_profile_ids
                ],
                "affected_tracks": len(affected_track_ids),
            })

    # ── Jobs: stuck in running state ─────────────────────────────────────────
    stuck_running_jobs = []
    for job in session.scalars(
        select(RipJob).where(RipJob.status == JobStatus.running)
    ).all():
        stuck_running_jobs.append({
            "job_id": job.id,
            "disc_id": job.disc_id,
            "disc_title": _disc_title(job.disc) if job.disc else f"Disc #{job.disc_id}",
            "drive_id": job.drive_id,
            "started_at": job.started_at.isoformat() if job.started_at else None,
        })

    # ── Summary ──────────────────────────────────────────────────────────────
    dvd_issues = (
        len(duplicate_dvd_discs) + len(missing_iso_files) + len(orphaned_iso_dirs)
        + len(null_raw_path) + len(stale_dvd_associations)
        + len(missing_dvd_encode_jobs)
    )
    cd_issues = (
        len(duplicate_cd_discs) + len(missing_wav_files) + len(orphaned_wav_dirs)
        + len(tracks_missing_wav_filename) + len(missing_cd_encode_jobs)
    )
    job_issues = len(stuck_running_jobs)
    missing_encode_jobs = len(missing_dvd_encode_jobs) + len(missing_cd_encode_jobs)

    return jsonify({
        "dvd": {
            "duplicate_discs": duplicate_dvd_discs,
            "missing_iso_files": missing_iso_files,
            "orphaned_iso_dirs": orphaned_iso_dirs,
            "null_raw_path": null_raw_path,
            "stale_drive_associations": stale_dvd_associations,
            "missing_encode_jobs": missing_dvd_encode_jobs,
        },
        "cd": {
            "duplicate_discs": duplicate_cd_discs,
            "missing_wav_files": missing_wav_files,
            "orphaned_wav_dirs": orphaned_wav_dirs,
            "tracks_missing_wav_filename": tracks_missing_wav_filename,
            "missing_encode_jobs": missing_cd_encode_jobs,
        },
        "jobs": {
            "stuck_running_jobs": stuck_running_jobs,
        },
        "summary": {
            "total_issues": dvd_issues + cd_issues + job_issues,
            "dvd_issues": dvd_issues,
            "cd_issues": cd_issues,
            "job_issues": job_issues,
            "missing_encode_jobs": missing_encode_jobs,
        },
    })
