"""
CD audio encoding: WAV → FLAC (flac) or WAV → MP3 (ffmpeg/libmp3lame).

Each public function is designed to be called from a background thread;
they manage their own DB sessions and never raise — all exceptions are
caught, logged, and written to the job's error_message.
"""

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from common.config import load_config, get_db_url
from common.models import EncodeJob, JobStatus, naive_utcnow
from encoder_service import process_registry

logger = logging.getLogger(__name__)

MIN_WAV_SIZE = 1 * 1024 * 1024  # 1 MB — skip fake/test files written as placeholders

_PROGRESS_THROTTLE_SECONDS = 2.0

# flac stderr: "  60% complete, ratio=0.543"
_FLAC_PROGRESS_RE = re.compile(r"(\d+)%\s+complete", re.IGNORECASE)

# ffmpeg stderr: "size=  1024kB time=00:01:23.45 bitrate=..."
_FFMPEG_TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")


def is_valid_wav(wav_path: str) -> bool:
    """
    Returns True only if the file exists and is at least MIN_WAV_SIZE bytes.
    Logs a warning and returns False for small/missing files (e.g. zero-byte
    files written by fake_rip_mode). Never raises.
    """
    try:
        size = os.path.getsize(wav_path)
        if size < MIN_WAV_SIZE:
            logger.warning(
                "WAV %s too small (%d bytes) - likely test/fake file, skipping",
                wav_path, size,
            )
            return False
        return True
    except FileNotFoundError:
        logger.warning("WAV %s not found", wav_path)
        return False
    except Exception:
        logger.exception("is_valid_wav: unexpected error checking %s", wav_path)
        return False


def _set_job_error(job_id: int, message: str, session_factory) -> None:
    session = session_factory()
    try:
        job = session.get(EncodeJob, job_id)
        if job is None or job.status != JobStatus.running:
            # Job was rolled back to queued by the shutdown handler; don't overwrite it.
            return
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


def encode_flac(
    input_wav: str,
    output_path: str,
    tool_params: dict,
    job_id: int,
    session_factory,
) -> bool:
    """
    Encodes input_wav to FLAC at output_path using flac.
    Compression level comes from tool_params["compression"] (default 8).
    Updates job progress every ~2 seconds by parsing flac's stderr output.
    Returns True on success, False on any failure. Never raises.
    """
    if not is_valid_wav(input_wav):
        _set_job_error(job_id, "Invalid/fake WAV file", session_factory)
        return False

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    except Exception as exc:
        _set_job_error(job_id, f"Failed to create output directory: {exc}", session_factory)
        return False

    compression = int((tool_params or {}).get("compression", 8))
    command = [
        "flac",
        f"--compression-level-{compression}",
        "-f",              # overwrite if output exists
        "-o", output_path,
        input_wav,
    ]

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
        process_registry.register(job_id, proc)
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                log_lines.append(line)
                now = time.time()
                if now - last_update >= _PROGRESS_THROTTLE_SECONDS:
                    m = _FLAC_PROGRESS_RE.search(line)
                    if m:
                        _update_job_progress(job_id, int(m.group(1)), "Encoding FLAC", session_factory)
                        last_update = now

            proc.wait()
        finally:
            process_registry.deregister(job_id)

        full_log = "\n".join(log_lines)

        session = session_factory()
        try:
            job = session.get(EncodeJob, job_id)
            if job is not None:
                job.log = full_log
                session.commit()
        finally:
            session.close()

        if proc.returncode != 0:
            _set_job_error(
                job_id,
                f"flac exited with code {proc.returncode}",
                session_factory,
            )
            return False

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            _set_job_error(job_id, "Output file missing or empty after encode", session_factory)
            return False

        logger.info(
            "FLAC encode complete: %s -> %s (%d bytes)",
            input_wav, output_path, os.path.getsize(output_path),
        )
        return True

    except Exception as exc:
        logger.exception("encode_flac crashed for job %s", job_id)
        _set_job_error(job_id, f"Unexpected error: {exc}", session_factory)
        return False


def encode_mp3(
    input_wav: str,
    output_path: str,
    tool_params: dict,
    job_id: int,
    session_factory,
    duration_seconds: float | None = None,
) -> bool:
    """
    Encodes input_wav to MP3 at output_path using ffmpeg/libmp3lame.
    Bitrate comes from tool_params["bitrate"] (default "320k").
    Parses ffmpeg's stderr time= lines to track percentage progress.
    Returns True on success, False on any failure. Never raises.
    """
    if not is_valid_wav(input_wav):
        _set_job_error(job_id, "Invalid/fake WAV file", session_factory)
        return False

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    except Exception as exc:
        _set_job_error(job_id, f"Failed to create output directory: {exc}", session_factory)
        return False

    bitrate = (tool_params or {}).get("bitrate", "320k")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-i", input_wav,
        "-codec:a", "libmp3lame",
        "-b:a", bitrate,
        "-y",              # overwrite without asking
        output_path,
    ]

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
        process_registry.register(job_id, proc)
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                log_lines.append(line)
                if duration_seconds and duration_seconds > 0:
                    now = time.time()
                    if now - last_update >= _PROGRESS_THROTTLE_SECONDS:
                        m = _FFMPEG_TIME_RE.search(line)
                        if m:
                            elapsed = (
                                int(m.group(1)) * 3600
                                + int(m.group(2)) * 60
                                + float(m.group(3))
                            )
                            pct = min(99, int(elapsed / duration_seconds * 100))
                            _update_job_progress(job_id, pct, "Encoding MP3", session_factory)
                            last_update = now

            proc.wait()
        finally:
            process_registry.deregister(job_id)

        full_log = "\n".join(log_lines)

        session = session_factory()
        try:
            job = session.get(EncodeJob, job_id)
            if job is not None:
                job.log = full_log
                session.commit()
        finally:
            session.close()

        if proc.returncode != 0:
            _set_job_error(
                job_id,
                f"ffmpeg exited with code {proc.returncode}",
                session_factory,
            )
            return False

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            _set_job_error(job_id, "Output file missing or empty after encode", session_factory)
            return False

        logger.info(
            "MP3 encode complete: %s -> %s (%d bytes)",
            input_wav, output_path, os.path.getsize(output_path),
        )
        return True

    except Exception as exc:
        logger.exception("encode_mp3 crashed for job %s", job_id)
        _set_job_error(job_id, f"Unexpected error: {exc}", session_factory)
        return False


def encode_cd_track(job_id: int, session_factory) -> None:
    """
    Main entry point called in a background thread.

    Loads the EncodeJob (with profile and CDTrack), computes input/output
    paths, marks the job running, dispatches to encode_flac or encode_mp3,
    then marks done or error.
    """
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    datastore_root = cfg["storage"]["datastore_root"]

    # Load job, profile, track and disc in one session, then extract every
    # needed attribute as a plain Python value before session.close().
    # Accessing ORM relationship attributes after the session closes raises
    # DetachedInstanceError.
    session = session_factory()
    try:
        job = session.get(EncodeJob, job_id)
        if job is None:
            logger.error("EncodeJob %s not found", job_id)
            return

        track = job.track
        if track is None:
            _set_job_error(job_id, "No CDTrack linked to this encode job", session_factory)
            return
        if track.wav_filename is None:
            _set_job_error(job_id, "CDTrack has no wav_filename - rip may be incomplete", session_factory)
            return

        disc = track.disc
        if disc is None:
            _set_job_error(job_id, "No Disc linked to this track", session_factory)
            return

        # Snapshot all ORM attributes as plain Python values.
        tool         = job.profile.tool
        profile_name = job.profile.name
        tool_params  = json.loads(job.profile.tool_params or '{}')
        output_folder = job.profile.output_folder
        wav_filename  = track.wav_filename
        track_number  = track.track_number
        disc_id       = job.disc_id        # scalar FK, safe but snapshot anyway
        disc_raw_path = disc.raw_path
        duration      = track.duration_seconds

        # Resolve paths.
        input_wav  = str(Path(datastore_root) / disc_raw_path / wav_filename)
        ext        = "flac" if tool == "flac" else "mp3"
        track_stem = f"track{track_number:02d}"
        output_dir = Path(datastore_root) / output_folder / str(disc_id)
        output_path = str(output_dir / f"{track_stem}.{ext}")

        # Create output directory (and all intermediate dirs such as
        # cd_store/flac/) before the subprocess runs.
        os.makedirs(output_dir, exist_ok=True)

        # Mark running.
        job.status = JobStatus.running
        job.started_at = naive_utcnow()
        job.source_file = input_wav
        job.output_path = output_path
        session.commit()
    finally:
        session.close()

    logger.info(
        "Starting CD encode job %s: %s -> %s (tool=%s)",
        job_id, input_wav, output_path, tool,
    )

    # Dispatch to the appropriate encoder using only plain Python values.
    if tool == "flac":
        success = encode_flac(input_wav, output_path, tool_params, job_id, session_factory)
    elif tool == "ffmpeg":
        success = encode_mp3(
            input_wav, output_path, tool_params, job_id, session_factory,
            duration_seconds=duration,
        )
    else:
        _set_job_error(job_id, f"Unknown tool '{tool}' in profile '{profile_name}'", session_factory)
        return

    if success:
        session = session_factory()
        try:
            job = session.get(EncodeJob, job_id)
            if job is not None:
                job.status = JobStatus.done
                job.completed_at = naive_utcnow()
                job.progress_percent = 100
                session.commit()
        except Exception:
            logger.exception("Failed to mark EncodeJob %s done", job_id)
        finally:
            session.close()
