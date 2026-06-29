"""
DVD/CD rip execution - runs dvdbackup+mkisofs or cdparanoia (or their
fake_rip_mode stand-ins) and streams progress back into the owning
rip_jobs row.

Blocking by design: the caller (job_starter.py) runs each of these in
its own background thread, so blocking here doesn't stall the main poll
loop.
"""

import logging
import os
import re
import subprocess
import time

from common.models import RipJob
from ripper_service import active_jobs

logger = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:done)?", re.IGNORECASE)
_DIRTY_RIP_RE = re.compile(r"Error reading.*padding", re.IGNORECASE)


def _is_dirty_rip_line(line: str) -> bool:
    return bool(_DIRTY_RIP_RE.search(line))


def detect_dirty_rip(log_text: str) -> bool:
    """
    True if dvdbackup's captured output shows it padded over a
    recoverable read error rather than failing outright (the dvdbackup
    process still exits 0 in that case). Kept isolated and
    unit-testable since the exact message format may need refinement
    against real-world dvdbackup logs.

    Used as a post-completion safety net in run_dvdbackup - the primary
    detection path checks each line live as it streams in, so this only
    ends up doing real work if a dirty line was somehow missed there
    (e.g. split across a buffered read boundary).
    """
    return any(_is_dirty_rip_line(line) for line in log_text.splitlines())


def _mark_dirty(disc) -> None:
    disc.rip_quality = "dirty"
    disc.needs_rerip = True


def _flag_dirty_rip_live(rip_job_id: int, line: str, session) -> bool:
    """
    Persist the dirty-rip flag the moment it's seen mid-rip, rather than
    waiting for dvdbackup to finish, so the UI can show the warning while
    the disc is still "ripping"/"building". Returns True if a disc was
    found and flagged (i.e. the caller can skip the post-completion
    fallback check).
    """
    rip_job = session.get(RipJob, rip_job_id)
    disc = rip_job.disc if rip_job else None
    if disc is None:
        return False

    _mark_dirty(disc)
    session.commit()
    logger.warning(
        "Dirty rip detected live for disc #%s - read error at %s",
        disc.id, line.strip(),
    )
    return True


def run_dvdbackup(device_path, scratch_dir, disc_label, fake_mode, rip_job_id, session_factory, inject_dirty: bool = False) -> dict:
    """
    Run dvdbackup (real or fake) for one disc, updating rip_job progress
    as output streams in. Also watches each line for a dirty-rip read
    error and flags the disc as soon as one is seen, rather than waiting
    for completion (see _flag_dirty_rip_live).

    inject_dirty only has an effect when fake_mode is also True - it
    tells the fake stand-in to simulate a read error partway through, for
    testing dirty-rip detection without real hardware (see job_starter's
    fake_dirty_mode handling, which decides when this is set).

    Returns {"success": bool, "log": str, "return_code": int|None}, plus
    "dirty": bool when success is True (see detect_dirty_rip).
    Never raises - any failure is captured and reflected in the result.
    """
    if fake_mode:
        command = [
            "python3", "-m", "ripper_service.fake_tools.fake_dvdbackup",
            "-i", device_path, "-o", scratch_dir, "-n", disc_label,
        ]
        if inject_dirty:
            command.append("--dirty")
    else:
        command = [
            "dvdbackup", "-p", "-M",
            "-i", device_path, "-o", scratch_dir, "-n", disc_label,
        ]

    log_lines = []
    session = session_factory()

    try:
        # Defense in depth: main.py creates this at service startup, but a
        # long-running service could outlive an externally-cleared /tmp
        # without a restart, so confirm it again right before use.
        os.makedirs(scratch_dir, exist_ok=True)

        if active_jobs.was_rolled_back(rip_job_id):
            # Rolled back between being queued for start and actually
            # launching (tight race) - don't bother starting the real work.
            logger.info("Rip job %s was rolled back before starting - skipping launch", rip_job_id)
            return {"success": False, "log": "Rolled back before starting", "return_code": None}

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        active_jobs.set_process(rip_job_id, proc)

        dirty_detected_live = False
        last_progress_write = 0.0
        progress_sample_logged = False
        for line in proc.stdout:
            line = line.rstrip("\n")
            log_lines.append(line)
            if not progress_sample_logged and _PROGRESS_RE.search(line):
                logger.info("dvdbackup progress line format (rip_job %s): %r", rip_job_id, line)
                progress_sample_logged = True
            last_progress_write = _maybe_update_progress(line, rip_job_id, session, last_write=last_progress_write)
            if not dirty_detected_live and _is_dirty_rip_line(line):
                dirty_detected_live = _flag_dirty_rip_live(rip_job_id, line, session)

        proc.wait()

        full_log = "\n".join(log_lines)
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is not None:
            rip_job.log = full_log
            session.commit()

        success = proc.returncode == 0
        result = {"success": success, "log": full_log, "return_code": proc.returncode}
        if success:
            if dirty_detected_live:
                dirty = True
            else:
                dirty = detect_dirty_rip(full_log)
                if dirty:
                    disc = rip_job.disc if rip_job else None
                    if disc is not None:
                        _mark_dirty(disc)
                        session.commit()
                        logger.warning(
                            "Dirty rip detected post-completion for disc #%s - missed "
                            "by the live per-line check, flagging now as a fallback",
                            disc.id,
                        )
            result["dirty"] = dirty
        return result

    except Exception as exc:
        logger.exception("run_dvdbackup crashed for rip_job %s", rip_job_id)
        full_log = "\n".join(log_lines) + f"\n\nException: {exc}"
        try:
            rip_job = session.get(RipJob, rip_job_id)
            if rip_job is not None:
                rip_job.log = full_log
                session.commit()
        except Exception:
            logger.exception("Failed to persist crash log for rip_job %s", rip_job_id)
        return {"success": False, "log": full_log, "return_code": None}

    finally:
        session.close()


def run_mkisofs(scratch_subdir, output_iso_path, fake_mode, rip_job_id, session_factory) -> dict:
    """
    Build an ISO from scratch_subdir (the directory dvdbackup wrote
    VIDEO_TS/AUDIO_TS into - real or fake), updating rip_job progress as
    output streams in.

    Returns {"success": bool, "log": str, "return_code": int|None}.
    Never raises - any failure is captured and reflected in the result.
    """
    if fake_mode:
        command = [
            "python3", "-m", "ripper_service.fake_tools.fake_mkisofs",
            "-o", output_iso_path, scratch_subdir,
        ]
    else:
        command = [
            "mkisofs", "-dvd-video", "-o", output_iso_path, scratch_subdir,
        ]

    log_lines = []
    session = session_factory()

    try:
        # dvd_store/raw/<disc_id>/ won't exist yet for a new disc.
        os.makedirs(os.path.dirname(output_iso_path), exist_ok=True)

        if active_jobs.was_rolled_back(rip_job_id):
            logger.info("Rip job %s was rolled back before mkisofs started - skipping launch", rip_job_id)
            return {"success": False, "log": "Rolled back before starting", "return_code": None}

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        active_jobs.set_process(rip_job_id, proc)

        last_progress_write = 0.0
        for line in proc.stdout:
            line = line.rstrip("\n")
            log_lines.append(line)
            last_progress_write = _maybe_update_progress(
                line, rip_job_id, session, stage_override="Building ISO", last_write=last_progress_write,
            )

        proc.wait()

        full_log = "\n".join(log_lines)
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is not None:
            # Append rather than overwrite, so the dvdbackup log from the
            # earlier sub-step is preserved alongside this one.
            rip_job.log = (rip_job.log + "\n\n--- mkisofs ---\n" if rip_job.log else "") + full_log
            session.commit()

        return {"success": proc.returncode == 0, "log": full_log, "return_code": proc.returncode}

    except Exception as exc:
        logger.exception("run_mkisofs crashed for rip_job %s", rip_job_id)
        full_log = "\n".join(log_lines) + f"\n\nException: {exc}"
        try:
            rip_job = session.get(RipJob, rip_job_id)
            if rip_job is not None:
                rip_job.log = (rip_job.log + "\n\n--- mkisofs ---\n" if rip_job.log else "") + full_log
                session.commit()
        except Exception:
            logger.exception("Failed to persist crash log for rip_job %s", rip_job_id)
        return {"success": False, "log": full_log, "return_code": None}

    finally:
        session.close()


_PROGRESS_THROTTLE_SECONDS = 3.0


def _maybe_update_progress(line: str, rip_job_id: int, session, stage_override: str | None = None, last_write: float = 0.0) -> float:
    match = _PROGRESS_RE.search(line)
    if not match:
        return last_write

    now = time.time()
    if now - last_write < _PROGRESS_THROTTLE_SECONDS:
        return last_write

    percent = int(float(match.group(1)))
    if stage_override is not None:
        stage = stage_override
    else:
        stage = line.split(":", 1)[0].strip() if ":" in line else line.strip()

    rip_job = session.get(RipJob, rip_job_id)
    if rip_job is None:
        return last_write

    rip_job.progress_percent = percent
    rip_job.progress_stage = stage
    session.commit()
    return now


# ---------------------------------------------------------------------------
# CD (cdparanoia) - one call per track, no separate build step.
# ---------------------------------------------------------------------------

# cdparanoia -e's callback format: "##: <code> [<op>] @ <byte>". Based on
# one real drive/disc observed during development (cdparanoia III 10.2) -
# "read"/"verify"/"overlap" show up on a clean rip; this set of "trouble"
# operation names may need refinement against other drives/discs/versions
# encountered in practice (same spirit as detect_dirty_rip above).
_CD_PROGRESS_RE = re.compile(r"##:\s*-?\d+\s*\[(\w[\w-]*)\]\s*@\s*(\d+)")
_DIRTY_TRACK_OPS = {"skip", "scratch", "dropped", "duped", "backoff", "drift"}

# 16-bit stereo PCM at 44.1kHz - used to turn a CDTrack's duration_seconds
# into an expected byte count for a rough progress percentage.
_CD_BYTES_PER_SECOND = 176400


def _is_dirty_track_line(line: str) -> bool:
    match = _CD_PROGRESS_RE.search(line)
    return bool(match and match.group(1).lower() in _DIRTY_TRACK_OPS)


def detect_dirty_track(log_text: str) -> bool:
    """
    Best-effort scan of cdparanoia's -e callback stream for trouble
    indicators (jitter/scratch/skip-style operations - see
    _DIRTY_TRACK_OPS) rather than the plain "read"/"verify"/"overlap"
    operations a clean rip shows.
    """
    return any(_is_dirty_track_line(line) for line in log_text.splitlines())


def _maybe_update_cd_progress(line: str, rip_job_id: int, session, stage_label: str, total_bytes, max_bytes_seen: int, last_write: float = 0.0) -> tuple[int, float]:
    """
    Updates rip_job.progress_stage/progress_percent from a cdparanoia
    callback line, returning (max_bytes_seen, last_write).

    cdparanoia's own byte counter resets across its internal overlap/
    verification passes rather than increasing monotonically over the
    whole track, so the running max is an approximation of position, not
    an exact one.

    DB writes are throttled to at most once every _PROGRESS_THROTTLE_SECONDS
    since cdparanoia emits a line per sector (potentially thousands per track).
    """
    match = _CD_PROGRESS_RE.search(line)
    if not match:
        return max_bytes_seen, last_write

    max_bytes_seen = max(max_bytes_seen, int(match.group(2)))

    now = time.time()
    if now - last_write < _PROGRESS_THROTTLE_SECONDS:
        return max_bytes_seen, last_write

    rip_job = session.get(RipJob, rip_job_id)
    if rip_job is None:
        return max_bytes_seen, last_write

    rip_job.progress_stage = stage_label
    if total_bytes:
        rip_job.progress_percent = min(99, int(max_bytes_seen / total_bytes * 100))
    session.commit()
    return max_bytes_seen, now


def run_cdparanoia(
    device_path, track_number, output_wav_path, duration_seconds, stage_label,
    fake_mode, rip_job_id, session_factory, inject_dirty: bool = False,
) -> dict:
    """
    Rip one CD track (real cdparanoia or its fake_rip_mode stand-in),
    updating rip_job progress as output streams in. No build step for
    CD - this writes the final WAV directly.

    duration_seconds (the CDTrack row's value, computed at TOC-read
    time) is used to estimate a percentage against total_bytes - see
    _maybe_update_cd_progress. May be None (couldn't be computed at
    detection time), in which case progress_stage still updates but
    progress_percent does not.

    inject_dirty only has an effect when fake_mode is also True - see
    job_starter's fake_dirty_mode handling, which decides when this is
    set (mirrors run_dvdbackup's inject_dirty).

    Returns {"success": bool, "log": str, "return_code": int|None}, plus
    "dirty": bool when success is True (see detect_dirty_track).
    Never raises - any failure is captured and reflected in the result.
    """
    total_bytes = duration_seconds * _CD_BYTES_PER_SECOND if duration_seconds else None

    if fake_mode:
        command = [
            "python3", "-m", "ripper_service.fake_tools.fake_cdparanoia",
            "-d", device_path, "-t", str(track_number), "-o", output_wav_path,
            "--total-bytes", str(int(total_bytes) if total_bytes else 0),
        ]
        if inject_dirty:
            command.append("--dirty")
    else:
        command = ["cdparanoia", "-e", "-d", device_path, str(track_number), output_wav_path]

    log_lines = []
    session = session_factory()
    max_bytes_seen = 0

    try:
        # dest dir won't exist yet for a new disc.
        os.makedirs(os.path.dirname(output_wav_path), exist_ok=True)

        if active_jobs.was_rolled_back(rip_job_id):
            logger.info(
                "Rip job %s was rolled back before track %s started - skipping launch",
                rip_job_id, track_number,
            )
            return {"success": False, "log": "Rolled back before starting", "return_code": None}

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        active_jobs.set_process(rip_job_id, proc)

        last_progress_write = 0.0
        for line in proc.stdout:
            line = line.rstrip("\n")
            log_lines.append(line)
            max_bytes_seen, last_progress_write = _maybe_update_cd_progress(
                line, rip_job_id, session, stage_label, total_bytes, max_bytes_seen, last_progress_write,
            )

        proc.wait()

        full_log = "\n".join(log_lines)
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is not None:
            rip_job.log = (rip_job.log + f"\n\n--- {stage_label} ---\n" if rip_job.log else "") + full_log
            if proc.returncode == 0:
                rip_job.progress_percent = 100
                rip_job.progress_stage = stage_label
            session.commit()

        success = proc.returncode == 0
        result = {"success": success, "log": full_log, "return_code": proc.returncode}
        if success:
            result["dirty"] = detect_dirty_track(full_log)
        return result

    except Exception as exc:
        logger.exception("run_cdparanoia crashed for rip_job %s track %s", rip_job_id, track_number)
        full_log = "\n".join(log_lines) + f"\n\nException: {exc}"
        try:
            rip_job = session.get(RipJob, rip_job_id)
            if rip_job is not None:
                rip_job.log = (rip_job.log + f"\n\n--- {stage_label} ---\n" if rip_job.log else "") + full_log
                session.commit()
        except Exception:
            logger.exception("Failed to persist crash log for rip_job %s track %s", rip_job_id, track_number)
        return {"success": False, "log": full_log, "return_code": None}

    finally:
        session.close()
