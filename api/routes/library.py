"""
Library API — symlink tree generation.

GET  /api/library/status   — prerequisite counts + last generation info
POST /api/library/generate — rebuild the library symlink tree
"""

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, current_app
from sqlalchemy import func, select

from common.catalog_dups import load_dup_roles
from common.cd_tagger import fetch_cover, tag_flac, tag_mp3
from common.models import (
    Catalog, CDTrack, Disc, DiscStatus, DiscType, EncodeJob, EncodeProfile,
    JobStatus, Setting, naive_utcnow,
)

library_bp = Blueprint("library", __name__)

_KEY_LAST_GENERATED = "library_last_generated"
_KEY_LAST_STATS = "library_last_stats"

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
            Disc.type == DiscType.dvd,
            Disc.status.in_(_ripping_statuses),
        )
    ) or 0

    cds_not_ripped = session.scalar(
        select(func.count(Disc.id)).where(
            Disc.type == DiscType.cd,
            Disc.status.in_(_ripping_statuses),
        )
    ) or 0

    prerequisites = {
        "dvds_unmatched": dvds_unmatched,
        "cds_unidentified": cds_unidentified,
        "cd_tracks_untitled": cd_tracks_untitled,
        "dvd_encodes_pending": dvd_encodes_pending,
        "cd_encodes_pending": cd_encodes_pending,
        "dvds_not_ripped": dvds_not_ripped,
        "cds_not_ripped": cds_not_ripped,
    }
    ready = all(v == 0 for v in prerequisites.values())

    # ── Last generation ───────────────────────────────────────────────────────
    last_generated_raw = _get_setting(session, _KEY_LAST_GENERATED)
    last_stats_raw = _get_setting(session, _KEY_LAST_STATS)

    last_generated = last_generated_raw or None
    last_stats = json.loads(last_stats_raw) if last_stats_raw else None

    return jsonify({
        "prerequisites": prerequisites,
        "ready": ready,
        "last_generated": last_generated,
        "last_stats": last_stats,
    })


@library_bp.route("/generate", methods=["POST"])
def generate_library():
    Session = current_app.config["DB_SESSION"]
    session = Session()
    cfg = current_app.config["DISCRIPPER_CFG"]
    store_root = Path(cfg["storage"]["datastore_root"])
    library_root = store_root / "library"

    t_start = time.monotonic()
    covers_dir = store_root / "cd_store" / "covers"
    stats = {
        "dvd_iso": 0,
        "dvd_plex": 0,
        "dvd_iphone": 0,
        "cd_flac": 0,
        "cd_mp3": 0,
        "cd_files_tagged": 0,
        "cd_covers_embedded": 0,
        "cd_albums_no_cover": 0,
        "errors": 0,
        "duration_seconds": 0.0,
    }

    # ── Wipe and recreate library tree ────────────────────────────────────────
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

    # ── Load DVD encode profiles by name ──────────────────────────────────────
    dvd_profiles = {
        p.name: p
        for p in session.scalars(
            select(EncodeProfile).where(
                EncodeProfile.media_type == "dvd",
                EncodeProfile.enabled == True,
            )
        ).all()
    }
    all_dvd_profile_ids = {p.id for p in dvd_profiles.values()}
    plex_profile = dvd_profiles.get("DVD Plex")
    iphone_profile = dvd_profiles.get("DVD iPhone")

    # ── DVD symlinks ──────────────────────────────────────────────────────────
    dup_roles = load_dup_roles(session)

    dvd_discs = session.scalars(
        select(Disc).where(
            Disc.type == DiscType.dvd,
            Disc.catalog_id.isnot(None),
            Disc.status == DiscStatus.ripped,
        )
    ).all()

    for disc in dvd_discs:
        # Check all DVD profiles have done jobs.
        done_profile_ids = {
            job.profile_id
            for job in session.scalars(
                select(EncodeJob).where(
                    EncodeJob.disc_id == disc.id,
                    EncodeJob.status == JobStatus.done,
                )
            ).all()
        }
        if not all_dvd_profile_ids.issubset(done_profile_ids):
            continue

        catalog = session.get(Catalog, disc.catalog_id)
        if catalog is None:
            continue

        year_part = f" ({catalog.year})" if catalog.year else ""
        dr = dup_roles.get(catalog.id)
        if dr and dr.link_suffix:
            link_name = _sanitize(f"{catalog.title}{year_part} [{dr.link_suffix}]")
        else:
            link_name = _sanitize(f"{catalog.title}{year_part}")

        # ISO symlink — find the actual .iso file
        raw_dir = store_root / "dvd_store" / "raw" / str(disc.id)
        iso_candidates = sorted(raw_dir.glob("*.iso")) if raw_dir.exists() else []
        if iso_candidates:
            iso_path = iso_candidates[0]
            symlink = library_root / "dvd" / "iso" / f"{link_name}.iso"
            try:
                rel = os.path.relpath(iso_path, symlink.parent)
                os.symlink(rel, symlink)
                stats["dvd_iso"] += 1
            except Exception:
                stats["errors"] += 1

        # Plex symlink
        if plex_profile:
            plex_job = session.scalars(
                select(EncodeJob).where(
                    EncodeJob.disc_id == disc.id,
                    EncodeJob.profile_id == plex_profile.id,
                    EncodeJob.status == JobStatus.done,
                )
            ).first()
            if plex_job and plex_job.output_path:
                plex_path = Path(plex_job.output_path)
                ext = plex_path.suffix or ".mp4"
                symlink = library_root / "dvd" / "plex" / f"{link_name}{ext}"
                try:
                    rel = os.path.relpath(plex_path, symlink.parent)
                    os.symlink(rel, symlink)
                    stats["dvd_plex"] += 1
                except Exception:
                    stats["errors"] += 1

        # iPhone symlink
        if iphone_profile:
            iphone_job = session.scalars(
                select(EncodeJob).where(
                    EncodeJob.disc_id == disc.id,
                    EncodeJob.profile_id == iphone_profile.id,
                    EncodeJob.status == JobStatus.done,
                )
            ).first()
            if iphone_job and iphone_job.output_path:
                iphone_path = Path(iphone_job.output_path)
                ext = iphone_path.suffix or ".mp4"
                symlink = library_root / "dvd" / "iphone" / f"{link_name}{ext}"
                try:
                    rel = os.path.relpath(iphone_path, symlink.parent)
                    os.symlink(rel, symlink)
                    stats["dvd_iphone"] += 1
                except Exception:
                    stats["errors"] += 1

    # ── Load CD encode profiles by folder suffix ──────────────────────────────
    cd_profiles = {
        p.name: p
        for p in session.scalars(
            select(EncodeProfile).where(
                EncodeProfile.media_type == "cd",
                EncodeProfile.enabled == True,
            )
        ).all()
    }
    all_cd_profile_ids = {p.id for p in cd_profiles.values()}
    flac_profile = cd_profiles.get("CD FLAC")
    mp3_profile = cd_profiles.get("CD MP3 320")

    # ── CD symlinks ───────────────────────────────────────────────────────────
    cd_discs = session.scalars(
        select(Disc).where(
            Disc.type == DiscType.cd,
            Disc.album_title.isnot(None),
            Disc.status == DiscStatus.ripped,
        )
    ).all()

    for disc in cd_discs:
        tracks = sorted(disc.tracks, key=lambda t: t.track_number)
        if not tracks:
            continue

        # Check all CD profiles have done jobs for all tracks with wav files.
        trackable = [t for t in tracks if t.wav_filename is not None]
        if not trackable:
            continue

        done_combos = {
            (job.track_id, job.profile_id)
            for job in session.scalars(
                select(EncodeJob).where(
                    EncodeJob.disc_id == disc.id,
                    EncodeJob.status == JobStatus.done,
                )
            ).all()
        }
        all_done = all(
            (track.id, pid) in done_combos
            for track in trackable
            for pid in all_cd_profile_ids
        )
        if not all_done:
            continue

        artist_raw = disc.album_artist or ""
        if "various" in artist_raw.lower():
            artist_folder = _sanitize("Various")
        else:
            artist_folder = _sanitize(artist_raw or "Unknown Artist")
        album_folder = _sanitize(disc.album_title)
        is_various = artist_folder == "Various"
        track_total = len(trackable)

        # ── Year from selected candidate ──────────────────────────────────────
        selected_cand = next((c for c in disc.lookup_candidates if c.selected), None)
        year = None
        if selected_cand and selected_cand.candidate_data:
            year = selected_cand.candidate_data.get("year")

        # ── Cover art ─────────────────────────────────────────────────────────
        cover_data = None
        if disc.mb_release_id:
            cover_data = fetch_cover(disc.id, disc.mb_release_id, covers_dir)
        if cover_data is None:
            stats["cd_albums_no_cover"] += 1

        # ── Tag and symlink each track ────────────────────────────────────────
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

            # FLAC: tag then symlink
            if flac_profile:
                flac_src = store_root / flac_profile.output_folder / str(disc.id) / f"{track_stem}.flac"
                if flac_src.exists():
                    try:
                        modified = tag_flac(flac_src, tag_dict, cover_data)
                        if modified:
                            stats["cd_files_tagged"] += 1
                            if cover_data:
                                stats["cd_covers_embedded"] += 1
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).warning(
                            "FLAC tag error disc=%s track=%s: %s", disc.id, track.track_number, e
                        )
                        stats["errors"] += 1
                symlink = library_root / "cd" / "flac" / artist_folder / album_folder / f"{track_name}.flac"
                try:
                    symlink.parent.mkdir(parents=True, exist_ok=True)
                    rel = os.path.relpath(flac_src, symlink.parent)
                    os.symlink(rel, symlink)
                    stats["cd_flac"] += 1
                except Exception:
                    stats["errors"] += 1

            # MP3: tag then symlink
            if mp3_profile:
                mp3_src = store_root / mp3_profile.output_folder / str(disc.id) / f"{track_stem}.mp3"
                if mp3_src.exists():
                    try:
                        modified = tag_mp3(mp3_src, tag_dict, cover_data)
                        if modified:
                            stats["cd_files_tagged"] += 1
                            if cover_data:
                                stats["cd_covers_embedded"] += 1
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).warning(
                            "MP3 tag error disc=%s track=%s: %s", disc.id, track.track_number, e
                        )
                        stats["errors"] += 1
                symlink = library_root / "cd" / "mp3" / artist_folder / album_folder / f"{track_name}.mp3"
                try:
                    symlink.parent.mkdir(parents=True, exist_ok=True)
                    rel = os.path.relpath(mp3_src, symlink.parent)
                    os.symlink(rel, symlink)
                    stats["cd_mp3"] += 1
                except Exception:
                    stats["errors"] += 1

        # ── cover.jpg in library album folders ────────────────────────────────
        if cover_data is not None:
            cover_src = covers_dir / f"{disc.id}.jpg"
            for view in ("flac", "mp3"):
                cover_dest = library_root / "cd" / view / artist_folder / album_folder / "cover.jpg"
                try:
                    cover_dest.parent.mkdir(parents=True, exist_ok=True)
                    if not cover_dest.exists():
                        rel = os.path.relpath(cover_src, cover_dest.parent)
                        os.symlink(rel, cover_dest)
                except Exception:
                    pass

    # ── Persist results ───────────────────────────────────────────────────────
    stats["duration_seconds"] = round(time.monotonic() - t_start, 2)
    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _set_setting(session, _KEY_LAST_GENERATED, now_iso)
    _set_setting(session, _KEY_LAST_STATS, json.dumps(stats))
    session.commit()

    return jsonify(stats)
