"""
Library API — hard-link tree generation.

GET  /api/library/status   — prerequisite counts + current/last generation info
POST /api/library/generate — start background generation (returns immediately)
"""

import errno
import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, current_app
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from common.catalog_dups import load_dup_roles
from common.cd_tagger import fetch_cover, tag_flac, tag_mp3
from common.config import get_db_url
from common import cover_tracker
from common.models import (
    Catalog, CDTrack, Disc, DiscStatus, DiscType, EncodeJob, EncodeProfile,
    JobStatus, Setting, naive_utcnow,
)

library_bp = Blueprint("library", __name__)
log = logging.getLogger(__name__)

# Settings keys — persistent job state
_KEY_LAST_GENERATED = "library_last_generated"
_KEY_LAST_STATS = "library_last_stats"
_KEY_GEN_STATUS = "library_gen_status"
_KEY_GEN_PROGRESS = "library_gen_progress"
_KEY_GEN_HEARTBEAT = "library_gen_heartbeat"
_KEY_GEN_STARTED = "library_gen_started_at"
_KEY_GEN_ERROR = "library_gen_error"

_GEN_STALE_SECONDS = 120

_REMAINING_INVALID = re.compile(r'[/\\|?*<>"]')


def _sanitize(name: str) -> str:
    name = name.replace(": ", " - ").replace(":", "-")
    return _REMAINING_INVALID.sub("_", name).strip()


def _get_setting(session, key: str) -> str:
    row = session.get(Setting, key)
    return row.value if row else ""


def _set_setting(session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


def _effective_gen_status(raw: str | None, heartbeat: str) -> str:
    if not raw or raw == "idle":
        return "idle"
    if raw != "running":
        return raw  # "done" | "failed"
    if not heartbeat:
        return "interrupted"
    try:
        hb_dt = datetime.fromisoformat(heartbeat)
        if (naive_utcnow() - hb_dt).total_seconds() > _GEN_STALE_SECONDS:
            return "interrupted"
    except ValueError:
        return "interrupted"
    return "running"


@library_bp.route("/status", methods=["GET"])
def get_status():
    Session = current_app.config["DB_SESSION"]
    session = Session()

    # ── Prerequisites ─────────────────────────────────────────────────────────
    dvds_unmatched = session.scalar(
        select(func.count(Disc.id)).where(
            Disc.type == DiscType.dvd,
            Disc.catalog_id.is_(None),
            Disc.status == DiscStatus.ripped,
        )
    ) or 0

    cds_unidentified = session.scalar(
        select(func.count(Disc.id)).where(
            Disc.type == DiscType.cd,
            Disc.album_title.is_(None),
            Disc.status == DiscStatus.ripped,
        )
    ) or 0

    cd_tracks_untitled = session.scalar(
        select(func.count(CDTrack.id))
        .join(Disc, CDTrack.disc_id == Disc.id)
        .where(
            Disc.type == DiscType.cd,
            Disc.status == DiscStatus.ripped,
            CDTrack.title.is_(None),
        )
    ) or 0

    dvd_encodes_pending = session.scalar(
        select(func.count(EncodeJob.id))
        .join(EncodeProfile, EncodeJob.profile_id == EncodeProfile.id)
        .where(
            EncodeProfile.media_type == "dvd",
            EncodeJob.status.in_([JobStatus.queued, JobStatus.running]),
        )
    ) or 0

    cd_encodes_pending = session.scalar(
        select(func.count(EncodeJob.id))
        .join(EncodeProfile, EncodeJob.profile_id == EncodeProfile.id)
        .where(
            EncodeProfile.media_type == "cd",
            EncodeJob.status.in_([JobStatus.queued, JobStatus.running]),
        )
    ) or 0

    _ripping_statuses = [
        DiscStatus.queued, DiscStatus.ripping, DiscStatus.building, DiscStatus.identifying,
    ]
    dvds_not_ripped = session.scalar(
        select(func.count(Disc.id)).where(
            Disc.type == DiscType.dvd, Disc.status.in_(_ripping_statuses),
        )
    ) or 0

    cds_not_ripped = session.scalar(
        select(func.count(Disc.id)).where(
            Disc.type == DiscType.cd, Disc.status.in_(_ripping_statuses),
        )
    ) or 0

    cfg = current_app.config["DISCRIPPER_CFG"]
    covers_dir_status = Path(cfg["storage"]["datastore_root"]) / "cd_store" / "covers"
    cd_covers_fetching = cover_tracker.count(covers_dir_status)

    prerequisites = {
        "dvds_unmatched": dvds_unmatched,
        "cds_unidentified": cds_unidentified,
        "cd_tracks_untitled": cd_tracks_untitled,
        "dvd_encodes_pending": dvd_encodes_pending,
        "cd_encodes_pending": cd_encodes_pending,
        "dvds_not_ripped": dvds_not_ripped,
        "cds_not_ripped": cds_not_ripped,
        "cd_covers_fetching": cd_covers_fetching,
    }
    ready = all(v == 0 for v in prerequisites.values())

    # ── Generation job state ──────────────────────────────────────────────────
    raw_gen = _get_setting(session, _KEY_GEN_STATUS)
    gen_hb = _get_setting(session, _KEY_GEN_HEARTBEAT)
    gen_status = _effective_gen_status(raw_gen, gen_hb)
    gen_progress_raw = _get_setting(session, _KEY_GEN_PROGRESS)
    gen_started = _get_setting(session, _KEY_GEN_STARTED)
    gen_error = _get_setting(session, _KEY_GEN_ERROR) or None

    try:
        gen_progress = json.loads(gen_progress_raw) if gen_progress_raw else None
    except ValueError:
        gen_progress = None

    # ── Last generation ───────────────────────────────────────────────────────
    last_generated = _get_setting(session, _KEY_LAST_GENERATED) or None
    last_stats_raw = _get_setting(session, _KEY_LAST_STATS)
    try:
        last_stats = json.loads(last_stats_raw) if last_stats_raw else None
    except ValueError:
        last_stats = None

    return jsonify({
        "prerequisites": prerequisites,
        "ready": ready,
        "last_generated": last_generated,
        "last_stats": last_stats,
        "gen_status": gen_status,
        "gen_progress": gen_progress,
        "gen_heartbeat": gen_hb or None,
        "gen_started_at": gen_started or None,
        "gen_error": gen_error,
    })


@library_bp.route("/generate", methods=["POST"])
def generate_library():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]
    db_url = get_db_url(cfg)
    datastore_root = cfg["storage"]["datastore_root"]

    # Atomic start: SELECT FOR UPDATE prevents two gunicorn workers racing.
    status_row = session.scalar(
        select(Setting).where(Setting.key == _KEY_GEN_STATUS).with_for_update()
    )
    raw_status = status_row.value if status_row else "idle"
    gen_hb = _get_setting(session, _KEY_GEN_HEARTBEAT)
    current_status = _effective_gen_status(raw_status, gen_hb)

    if current_status == "running":
        session.rollback()
        return jsonify({"error": "already_running"}), 409

    now = naive_utcnow().isoformat()
    _set_setting(session, _KEY_GEN_STATUS, "running")
    _set_setting(session, _KEY_GEN_HEARTBEAT, now)
    _set_setting(session, _KEY_GEN_STARTED, now)
    _set_setting(session, _KEY_GEN_ERROR, "")
    _set_setting(session, _KEY_GEN_PROGRESS, json.dumps({
        "step": "Starting…", "discs_done": 0, "discs_total": 0,
    }))
    session.commit()

    t = threading.Thread(target=_run_library_gen, args=(db_url, datastore_root), daemon=True)
    t.start()

    return jsonify({"status": "started"}), 202


# ── Background generation ──────────────────────────────────────────────────────

def _run_library_gen(db_url: str, datastore_root: str) -> None:
    """Background thread: full library generation."""
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    store_root = Path(datastore_root)
    library_root = store_root / "library"
    covers_dir = store_root / "cd_store" / "covers"
    t_start = time.monotonic()

    def _hb(step: str, done: int, total: int) -> None:
        with Session() as s:
            _set_setting(s, _KEY_GEN_STATUS, "running")
            _set_setting(s, _KEY_GEN_PROGRESS, json.dumps({
                "step": step, "discs_done": done, "discs_total": total,
            }))
            _set_setting(s, _KEY_GEN_HEARTBEAT, naive_utcnow().isoformat())
            s.commit()

    try:
        stats = {
            "dvd_iso": 0, "dvd_plex": 0, "dvd_iphone": 0,
            "cd_flac": 0, "cd_mp3": 0,
            "cd_albums_with_cover": 0, "cd_albums_no_cover": 0,
            "cd_tracks_total": 0, "cd_files_updated": 0,
            "errors": 0, "duration_seconds": 0.0,
        }

        # ── Wipe and recreate library tree ─────────────────────────────────────
        _hb("Wiping library tree", 0, 0)
        if library_root.exists():
            shutil.rmtree(library_root)
        for subdir in [
            library_root / "dvd" / "iso",
            library_root / "dvd" / "plex",
            library_root / "dvd" / "iphone",
            library_root / "cd" / "flac",
            library_root / "cd" / "mp3",
        ]:
            subdir.mkdir(parents=True, exist_ok=True)

        # ── Load profiles and qualifying discs in one pass ─────────────────────
        with Session() as s:
            # DVD profiles
            dvd_profs = {
                p.name: (p.id, p.output_folder)
                for p in s.scalars(
                    select(EncodeProfile).where(
                        EncodeProfile.media_type == "dvd",
                        EncodeProfile.enabled == True,
                    )
                ).all()
            }
            all_dvd_pids = {v[0] for v in dvd_profs.values()}
            plex_prof = dvd_profs.get("DVD Plex")
            iphone_prof = dvd_profs.get("DVD iPhone")

            # CD profiles
            cd_profs = {
                p.name: (p.id, p.output_folder)
                for p in s.scalars(
                    select(EncodeProfile).where(
                        EncodeProfile.media_type == "cd",
                        EncodeProfile.enabled == True,
                    )
                ).all()
            }
            all_cd_pids = {v[0] for v in cd_profs.values()}
            flac_prof = cd_profs.get("CD FLAC")
            mp3_prof = cd_profs.get("CD MP3 320")

            # dup_roles is a dict of {catalog_id: DupRole(NamedTuple)} — safe outside session
            dup_roles = load_dup_roles(s)

            # Qualifying DVD discs — include ripped/encoding/done in case the
            # encoder ever updates disc.status (currently it doesn't, but the
            # completeness guard below ensures only fully-encoded discs link out).
            _has_iso = [DiscStatus.ripped, DiscStatus.encoding, DiscStatus.done]
            dvd_disc_ids = []
            for disc in s.scalars(
                select(Disc).where(
                    Disc.type == DiscType.dvd,
                    Disc.catalog_id.isnot(None),
                    Disc.status.in_(_has_iso),
                )
            ).all():
                done_pids = {
                    j.profile_id for j in s.scalars(
                        select(EncodeJob).where(
                            EncodeJob.disc_id == disc.id,
                            EncodeJob.status == JobStatus.done,
                        )
                    ).all()
                }
                if all_dvd_pids.issubset(done_pids):
                    dvd_disc_ids.append(disc.id)

            # Qualifying CD discs
            cd_disc_ids = []
            for disc in s.scalars(
                select(Disc).where(
                    Disc.type == DiscType.cd,
                    Disc.album_title.isnot(None),
                    Disc.status.in_(_has_iso),
                )
            ).all():
                trackable = [t for t in disc.tracks if t.wav_filename is not None]
                if not trackable:
                    continue
                done_combos = {
                    (j.track_id, j.profile_id) for j in s.scalars(
                        select(EncodeJob).where(
                            EncodeJob.disc_id == disc.id,
                            EncodeJob.status == JobStatus.done,
                        )
                    ).all()
                }
                if all((t.id, pid) in done_combos for t in trackable for pid in all_cd_pids):
                    cd_disc_ids.append(disc.id)

        total_discs = len(dvd_disc_ids) + len(cd_disc_ids)
        disc_count = 0
        _hb("Scanning discs", 0, total_discs)

        # ── DVD hard links ─────────────────────────────────────────────────────
        for disc_id in dvd_disc_ids:
            with Session() as s:
                disc = s.get(Disc, disc_id)
                if disc is None:
                    continue
                catalog = s.get(Catalog, disc.catalog_id)
                if catalog is None:
                    continue

                disc_count += 1
                _hb(f"DVD {disc_count}/{total_discs}: {catalog.title}", disc_count, total_discs)

                year_part = f" ({catalog.year})" if catalog.year else ""
                dr = dup_roles.get(catalog.id)
                if dr and dr.link_suffix:
                    link_name = _sanitize(f"{catalog.title}{year_part} [{dr.link_suffix}]")
                else:
                    link_name = _sanitize(f"{catalog.title}{year_part}")

                raw_dir = store_root / "dvd_store" / "raw" / str(disc.id)
                iso_candidates = sorted(raw_dir.glob("*.iso")) if raw_dir.exists() else []
                if iso_candidates:
                    iso_path = iso_candidates[0]
                    dest = library_root / "dvd" / "iso" / f"{link_name}.iso"
                    try:
                        os.link(iso_path, dest)
                        stats["dvd_iso"] += 1
                    except OSError as e:
                        log.error("Hard link failed %s: %s%s", dest.name, e,
                                  " (cross-device)" if e.errno == errno.EXDEV else "")
                        stats["errors"] += 1

                if plex_prof:
                    plex_job = s.scalars(
                        select(EncodeJob).where(
                            EncodeJob.disc_id == disc.id,
                            EncodeJob.profile_id == plex_prof[0],
                            EncodeJob.status == JobStatus.done,
                        )
                    ).first()
                    if plex_job and plex_job.output_path:
                        plex_path = Path(plex_job.output_path)
                        ext = plex_path.suffix or ".mp4"
                        dest = library_root / "dvd" / "plex" / f"{link_name}{ext}"
                        try:
                            os.link(plex_path, dest)
                            stats["dvd_plex"] += 1
                        except OSError as e:
                            log.error("Hard link failed %s: %s%s", dest.name, e,
                                      " (cross-device)" if e.errno == errno.EXDEV else "")
                            stats["errors"] += 1

                if iphone_prof:
                    iphone_job = s.scalars(
                        select(EncodeJob).where(
                            EncodeJob.disc_id == disc.id,
                            EncodeJob.profile_id == iphone_prof[0],
                            EncodeJob.status == JobStatus.done,
                        )
                    ).first()
                    if iphone_job and iphone_job.output_path:
                        iphone_path = Path(iphone_job.output_path)
                        ext = iphone_path.suffix or ".mp4"
                        dest = library_root / "dvd" / "iphone" / f"{link_name}{ext}"
                        try:
                            os.link(iphone_path, dest)
                            stats["dvd_iphone"] += 1
                        except OSError as e:
                            log.error("Hard link failed %s: %s%s", dest.name, e,
                                      " (cross-device)" if e.errno == errno.EXDEV else "")
                            stats["errors"] += 1

        # ── CD hard links + tagging ───────────────────────────────────────────
        for disc_id in cd_disc_ids:
            with Session() as s:
                disc = s.get(Disc, disc_id)
                if disc is None:
                    continue

                tracks = sorted(disc.tracks, key=lambda t: t.track_number)
                trackable = [t for t in tracks if t.wav_filename is not None]
                if not trackable:
                    continue

                disc_count += 1
                _hb(f"CD {disc_count}/{total_discs}: {disc.album_title}", disc_count, total_discs)

                artist_raw = disc.album_artist or ""
                if "various" in artist_raw.lower():
                    artist_folder = _sanitize("Various")
                else:
                    artist_folder = _sanitize(artist_raw or "Unknown Artist")
                album_folder = _sanitize(disc.album_title)
                is_various = artist_folder == "Various"
                track_total = len(trackable)

                # Year from selected candidate
                selected_cand = next((c for c in disc.lookup_candidates if c.selected), None)
                year = None
                if selected_cand and selected_cand.candidate_data:
                    year = selected_cand.candidate_data.get("year")

                # Cover art — check filesystem first (same as catalogue's has_cover)
                cover_data = None
                cover_path = covers_dir / f"{disc.id}.jpg"
                if cover_path.exists():
                    cover_data = cover_path.read_bytes()
                elif disc.mb_release_id:
                    cover_data = fetch_cover(disc.id, disc.mb_release_id, covers_dir)
                if cover_data is None:
                    stats["cd_albums_no_cover"] += 1
                else:
                    stats["cd_albums_with_cover"] += 1

                for track in trackable:
                    track_artist = track.artist if is_various else (disc.album_artist or "")
                    tag_dict = {
                        "title": track.title or "",
                        "artist": track_artist or "",
                        "album": disc.album_title or "",
                        "albumartist": disc.album_artist or "",
                        "tracknumber": track.track_number,
                        "tracktotal": track_total,
                        "discnumber": disc.mb_medium_position,
                        "disctotal": disc.mb_medium_count,
                        "year": year,
                    }

                    prefix = f"{track.track_number:02d}"
                    if is_various:
                        track_name = f"{prefix} - {_sanitize(track.title or 'Unknown')} [{_sanitize(track.artist or '')}]"
                    else:
                        track_name = f"{prefix} - {_sanitize(track.title or 'Unknown')}"
                    track_stem = f"track{track.track_number:02d}"

                    if flac_prof:
                        flac_src = store_root / flac_prof[1] / str(disc.id) / f"{track_stem}.flac"
                        if flac_src.exists():
                            stats["cd_tracks_total"] += 1
                            try:
                                if tag_flac(flac_src, tag_dict, cover_data):
                                    stats["cd_files_updated"] += 1
                            except Exception as e:
                                log.warning("FLAC tag error disc=%s track=%s: %s", disc.id, track.track_number, e)
                                stats["errors"] += 1
                        dest = library_root / "cd" / "flac" / artist_folder / album_folder / f"{track_name}.flac"
                        try:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            os.link(flac_src, dest)
                            stats["cd_flac"] += 1
                        except OSError as e:
                            log.error("Hard link failed %s: %s%s", dest.name, e,
                                      " (cross-device)" if e.errno == errno.EXDEV else "")
                            stats["errors"] += 1

                    if mp3_prof:
                        mp3_src = store_root / mp3_prof[1] / str(disc.id) / f"{track_stem}.mp3"
                        if mp3_src.exists():
                            stats["cd_tracks_total"] += 1
                            try:
                                if tag_mp3(mp3_src, tag_dict, cover_data):
                                    stats["cd_files_updated"] += 1
                            except Exception as e:
                                log.warning("MP3 tag error disc=%s track=%s: %s", disc.id, track.track_number, e)
                                stats["errors"] += 1
                        dest = library_root / "cd" / "mp3" / artist_folder / album_folder / f"{track_name}.mp3"
                        try:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            os.link(mp3_src, dest)
                            stats["cd_mp3"] += 1
                        except OSError as e:
                            log.error("Hard link failed %s: %s%s", dest.name, e,
                                      " (cross-device)" if e.errno == errno.EXDEV else "")
                            stats["errors"] += 1

                # cover.jpg in library album folders
                if cover_data is not None:
                    cover_src = covers_dir / f"{disc.id}.jpg"
                    for view in ("flac", "mp3"):
                        cover_dest = (
                            library_root / "cd" / view / artist_folder / album_folder / "cover.jpg"
                        )
                        try:
                            cover_dest.parent.mkdir(parents=True, exist_ok=True)
                            if not cover_dest.exists():
                                os.link(cover_src, cover_dest)
                        except OSError as e:
                            log.error("Hard link failed %s: %s%s", cover_dest.name, e,
                                      " (cross-device)" if e.errno == errno.EXDEV else "")
                            stats["errors"] += 1

        # ── Persist final results ──────────────────────────────────────────────
        stats["duration_seconds"] = round(time.monotonic() - t_start, 2)
        now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with Session() as s:
            _set_setting(s, _KEY_LAST_GENERATED, now_iso)
            _set_setting(s, _KEY_LAST_STATS, json.dumps(stats))
            _set_setting(s, _KEY_GEN_STATUS, "done")
            _set_setting(s, _KEY_GEN_HEARTBEAT, naive_utcnow().isoformat())
            _set_setting(s, _KEY_GEN_PROGRESS, json.dumps({
                "step": "Done", "discs_done": total_discs, "discs_total": total_discs,
            }))
            s.commit()

    except Exception as e:
        log.exception("Library generation failed")
        with Session() as s:
            _set_setting(s, _KEY_GEN_STATUS, "failed")
            _set_setting(s, _KEY_GEN_ERROR, str(e))
            _set_setting(s, _KEY_GEN_HEARTBEAT, naive_utcnow().isoformat())
            s.commit()
