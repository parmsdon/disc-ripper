"""
DVD encoding: ISO → MKV stream copy (extract), then MKV → H.264 MKV/MP4 (transcode).

Profiles:
  DVD Extract: ISO  → MKV via HandBrakeCLI stream copy (no re-encode)
  DVD Plex:    MKV  → H.264 MKV via HandBrakeCLI preset
  DVD iPhone:  MKV  → H.264 MP4 via HandBrakeCLI preset

All public functions run in background threads; they manage their own DB
sessions and never raise — exceptions are caught, logged, and stored in
the job's error_message.
"""

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from sqlalchemy import select

from common.config import load_config
from common.models import EncodeJob, JobStatus, naive_utcnow

logger = logging.getLogger(__name__)

MIN_ISO_SIZE = 100 * 1024 * 1024  # 100 MB — real DVDs are always larger

_DVD_PROGRESS_THROTTLE_SECONDS = 5.0

# HandBrake stdout: "Encoding: task 1 of 1, 12.50 % (195.23 fps, ...)"
_HB_PROGRESS_RE = re.compile(r"Encoding: task \d+ of \d+,\s+([\d.]+)\s*%", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def is_valid_iso(iso_path: str) -> bool:
    """
    Returns True only if the file exists and is >= MIN_ISO_SIZE bytes.
    Logs a warning and returns False for missing or suspiciously small files.
    Never raises.
    """
    try:
        size = os.path.getsize(iso_path)
        if size < MIN_ISO_SIZE:
            logger.warning(
                "ISO %s too small (%d bytes) - likely test/fake file, skipping",
                iso_path, size,
            )
            return False
        return True
    except FileNotFoundError:
        logger.warning("ISO %s not found", iso_path)
        return False
    except Exception:
        logger.exception("is_valid_iso: unexpected error checking %s", iso_path)
        return False


def find_iso_path(datastore_root: str, disc_id: int, disc_fingerprint: str) -> str | None:
    """
    Locates the ISO for a disc.

    Primary:  <datastore_root>/dvd_store/raw/<disc_id>/<disc_fingerprint>.iso
    Fallback: any .iso file in that directory (logs a warning if >1 found).

    Returns the path string if found, None otherwise.
    """
    raw_dir = Path(datastore_root) / "dvd_store" / "raw" / str(disc_id)

    # Primary: fingerprint-named ISO written by the ripper.
    if disc_fingerprint:
        primary = raw_dir / f"{disc_fingerprint}.iso"
        if primary.exists():
            return str(primary)

    # Fallback: any .iso in the directory (handles renamed or legacy files).
    try:
        isos = sorted(raw_dir.glob("*.iso"))
        if isos:
            if len(isos) > 1:
                logger.warning(
                    "Multiple ISOs in %s - using %s", raw_dir, isos[0].name,
                )
            return str(isos[0])
    except Exception:
        logger.exception("find_iso_path: error scanning %s", raw_dir)

    logger.warning("No ISO found in %s (disc_id=%s, fingerprint=%s)", raw_dir, disc_id, disc_fingerprint)
    return None


# ---------------------------------------------------------------------------
# Shared DB helpers
# ---------------------------------------------------------------------------

def _set_job_error(job_id: int, message: str, session_factory) -> None:
    session = session_factory()
    try:
        job = session.get(EncodeJob, job_id)
        if job is not None:
            job.status = JobStatus.error
            job.error_message = message[:1000]
            job.completed_at = naive_utcnow()
            session.commit()
    except Exception:
        logger.exception("Failed to set error state on EncodeJob %s", job_id)
    finally:
        session.close()


def _update_job_progress(job_id: int, percent: int, stage: str, session_factory) -> None:
    session = session_factory()
    try:
        job = session.get(EncodeJob, job_id)
        if job is not None:
            job.progress_percent = percent
            job.progress_stage = stage
            session.commit()
    except Exception:
        logger.exception("Failed to update progress for EncodeJob %s", job_id)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# HandBrake subprocess runner (shared by extract and transcode)
# ---------------------------------------------------------------------------

def _run_handbrake(
    command: list[str],
    job_id: int,
    stage: str,
    session_factory,
) -> dict:
    """
    Runs a HandBrakeCLI command, streams combined stdout/stderr, parses
    progress lines, and updates job progress at most every
    _DVD_PROGRESS_THROTTLE_SECONDS seconds.

    Returns {"success": bool, "log": str}. Never raises.
    """
    log_lines: list[str] = []
    last_update = 0.0

    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in proc.stdout:
            line = line.rstrip("\n")
            log_lines.append(line)
            now = time.time()
            if now - last_update >= _DVD_PROGRESS_THROTTLE_SECONDS:
                m = _HB_PROGRESS_RE.search(line)
                if m:
                    pct = min(99, int(float(m.group(1))))
                    _update_job_progress(job_id, pct, stage, session_factory)
                    last_update = now

        proc.wait()
        full_log = "\n".join(log_lines)

        session = session_factory()
        try:
            job = session.get(EncodeJob, job_id)
            if job is not None:
                job.log = full_log
                session.commit()
        finally:
            session.close()

        return {"success": proc.returncode == 0, "log": full_log}

    except Exception as exc:
        full_log = "\n".join(log_lines) + f"\n\nException: {exc}"
        logger.exception("_run_handbrake crashed for job %s", job_id)
        return {"success": False, "log": full_log}


# ---------------------------------------------------------------------------
# Encode functions
# ---------------------------------------------------------------------------

def encode_extract(
    iso_path: str,
    output_path: str,
    tool_params: dict,
    job_id: int,
    session_factory,
) -> bool:
    """
    Extracts the main DVD feature to MKV using stream copy (no re-encode).
    Returns True on success, False on any failure. Never raises.
    """
    if not is_valid_iso(iso_path):
        _set_job_error(job_id, "Invalid/small ISO file", session_factory)
        return False

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    except Exception as exc:
        _set_job_error(job_id, f"Failed to create output directory: {exc}", session_factory)
        return False

    command = [
        "HandBrakeCLI",
        "-i", iso_path,
        "--main-feature",
        "-o", output_path,
        "-f", "av_mkv",
        "--video-copy-mask", "H264,H265,MPEG2,MPEG4,VP8,VP9,AV1",
        "--audio-copy-mask", "AAC,AC3,EAC3,DTS,MP3,FLAC",
        "--aencoder", "copy",
        "--vencoder", "copy",
        "--no-dvdnav",
    ]

    result = _run_handbrake(command, job_id, "Extracting main feature", session_factory)

    if not result["success"]:
        _set_job_error(job_id, "HandBrakeCLI exited non-zero during extract", session_factory)
        return False

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        _set_job_error(job_id, "Output file missing or empty after extract", session_factory)
        return False

    logger.info(
        "DVD extract complete: %s -> %s (%d bytes)",
        iso_path, output_path, os.path.getsize(output_path),
    )
    return True


def encode_transcode(
    input_path: str,
    output_path: str,
    tool_params: dict,
    job_id: int,
    session_factory,
) -> bool:
    """
    Transcodes input_path to output_path using a HandBrake preset.
    Used for both DVD Plex and DVD iPhone profiles.
    Returns True on success, False on any failure. Never raises.
    """
    try:
        size = os.path.getsize(input_path)
        if size == 0:
            _set_job_error(job_id, f"Input file is empty: {input_path}", session_factory)
            return False
    except FileNotFoundError:
        _set_job_error(job_id, f"Input file not found: {input_path}", session_factory)
        return False
    except Exception as exc:
        _set_job_error(job_id, f"Failed to check input file: {exc}", session_factory)
        return False

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    except Exception as exc:
        _set_job_error(job_id, f"Failed to create output directory: {exc}", session_factory)
        return False

    preset = (tool_params or {}).get("preset", "")
    command = [
        "HandBrakeCLI",
        "-i", input_path,
        "-o", output_path,
        "--preset", preset,
    ]

    result = _run_handbrake(
        command, job_id, f"Transcoding ({preset})", session_factory,
    )

    if not result["success"]:
        _set_job_error(
            job_id,
            f"HandBrakeCLI exited non-zero during transcode (preset: {preset!r})",
            session_factory,
        )
        return False

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        _set_job_error(job_id, "Output file missing or empty after transcode", session_factory)
        return False

    logger.info(
        "DVD transcode complete: %s -> %s (%d bytes)",
        input_path, output_path, os.path.getsize(output_path),
    )
    return True


# ---------------------------------------------------------------------------
# Thread entry point
# ---------------------------------------------------------------------------

def encode_dvd_title(job_id: int, session_factory) -> None:
    """
    Thread entry point for DVD encoding.

    Loads the EncodeJob and extracts every needed ORM attribute as a plain
    Python value before session.close() (preventing DetachedInstanceError),
    then dispatches to encode_extract (DVD Extract profile, no dependency)
    or encode_transcode (DVD Plex / DVD iPhone, depends on extract).
    """
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    datastore_root = cfg["storage"]["datastore_root"]

    session = session_factory()
    try:
        job = session.get(EncodeJob, job_id)
        if job is None:
            logger.error("EncodeJob %s not found", job_id)
            return

        disc = job.disc
        if disc is None:
            _set_job_error(job_id, "No Disc linked to this encode job", session_factory)
            return

        # Snapshot all ORM attributes as plain Python before session close.
        profile_name          = job.profile.name
        tool_params           = json.loads(job.profile.tool_params or '{}')
        output_folder         = job.profile.output_folder
        depends_on_profile_id = job.profile.depends_on_profile_id
        disc_id               = job.disc_id
        disc_fingerprint      = disc.disc_fingerprint
        disc_temp_name        = disc.temp_name

        # Directory label: prefer temp_name, fall back to "disc_<id>".
        dir_name = disc_temp_name or f"disc_{disc_id}"

        is_extract = depends_on_profile_id is None

        if is_extract:
            iso_path = find_iso_path(datastore_root, disc_id, disc_fingerprint)
            if iso_path is None:
                _set_job_error(
                    job_id,
                    f"ISO not found for disc #{disc_id} (fingerprint={disc_fingerprint})",
                    session_factory,
                )
                return
            input_path = iso_path
            output_ext = "mkv"

        else:
            # Find the completed extract job for this disc — its output is our input.
            extract_job = session.scalars(
                select(EncodeJob).where(
                    EncodeJob.disc_id == disc_id,
                    EncodeJob.profile_id == depends_on_profile_id,
                    EncodeJob.status == JobStatus.done,
                )
            ).first()

            if extract_job is None:
                _set_job_error(
                    job_id,
                    f"Dependency extract job (profile_id={depends_on_profile_id}) "
                    f"not done for disc #{disc_id}",
                    session_factory,
                )
                return

            input_path = extract_job.output_path
            if not input_path:
                _set_job_error(
                    job_id, "Dependency extract job has no output_path recorded",
                    session_factory,
                )
                return

            # Output extension: MKV for presets that say so, MP4 for everything else
            # (e.g. Apple presets output M4V/MP4; HandBrake will write .mp4).
            preset = tool_params.get("preset", "")
            output_ext = "mkv" if "mkv" in preset.lower() else "mp4"

        output_dir  = Path(datastore_root) / output_folder / dir_name
        output_path = str(output_dir / f"movie.{output_ext}")

        os.makedirs(output_dir, exist_ok=True)

        job.status      = JobStatus.running
        job.started_at  = naive_utcnow()
        job.source_file = input_path
        job.output_path = output_path
        session.commit()

    finally:
        session.close()

    logger.info(
        "Starting DVD encode job %s (%s): %s -> %s",
        job_id, profile_name, input_path, output_path,
    )

    if is_extract:
        success = encode_extract(input_path, output_path, tool_params, job_id, session_factory)
    else:
        success = encode_transcode(input_path, output_path, tool_params, job_id, session_factory)

    if success:
        session = session_factory()
        try:
            job = session.get(EncodeJob, job_id)
            if job is not None:
                job.status           = JobStatus.done
                job.completed_at     = naive_utcnow()
                job.progress_percent = 100
                session.commit()
        except Exception:
            logger.exception("Failed to mark EncodeJob %s done", job_id)
        finally:
            session.close()
