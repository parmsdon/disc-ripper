"""
DVD rip execution - runs dvdbackup and mkisofs (or their fake_rip_mode
stand-ins) and streams progress back into the owning rip_jobs row.

Blocking by design: the caller (job_starter.py) runs each of these in
its own background thread, so blocking here doesn't stall the main poll
loop.
"""

import logging
import os
import re
import subprocess

from common.models import RipJob
from ripper_service import active_jobs

logger = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:done)?", re.IGNORECASE)


def run_dvdbackup(device_path, scratch_dir, disc_label, fake_mode, rip_job_id, session_factory) -> dict:
    """
    Run dvdbackup (real or fake) for one disc, updating rip_job progress
    as output streams in.

    Returns {"success": bool, "log": str, "return_code": int|None}.
    Never raises - any failure is captured and reflected in the result.
    """
    if fake_mode:
        command = [
            "python3", "-m", "ripper_service.fake_tools.fake_dvdbackup",
            "-i", device_path, "-o", scratch_dir, "-n", disc_label,
        ]
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

        for line in proc.stdout:
            line = line.rstrip("\n")
            log_lines.append(line)
            _maybe_update_progress(line, rip_job_id, session)

        proc.wait()

        full_log = "\n".join(log_lines)
        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is not None:
            rip_job.log = full_log
            session.commit()

        return {"success": proc.returncode == 0, "log": full_log, "return_code": proc.returncode}

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

        for line in proc.stdout:
            line = line.rstrip("\n")
            log_lines.append(line)
            _maybe_update_progress(line, rip_job_id, session, stage_override="Building ISO")

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


def _maybe_update_progress(line: str, rip_job_id: int, session, stage_override: str | None = None) -> None:
    match = _PROGRESS_RE.search(line)
    if not match:
        return

    percent = int(float(match.group(1)))
    if stage_override is not None:
        stage = stage_override
    else:
        stage = line.split(":", 1)[0].strip() if ":" in line else line.strip()

    rip_job = session.get(RipJob, rip_job_id)
    if rip_job is None:
        return

    rip_job.progress_percent = percent
    rip_job.progress_stage = stage
    session.commit()
