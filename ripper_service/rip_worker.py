"""
DVD rip execution - runs dvdbackup (or the fake_rip_mode stand-in) and
streams progress back into the owning rip_jobs row.

Blocking by design: the caller (job_starter.py) runs this in its own
background thread, so blocking here doesn't stall the main poll loop.
"""

import logging
import re
import subprocess

from common.models import RipJob

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


def _maybe_update_progress(line: str, rip_job_id: int, session) -> None:
    match = _PROGRESS_RE.search(line)
    if not match:
        return

    percent = int(float(match.group(1)))
    stage = line.split(":", 1)[0].strip() if ":" in line else line.strip()

    rip_job = session.get(RipJob, rip_job_id)
    if rip_job is None:
        return

    rip_job.progress_percent = percent
    rip_job.progress_stage = stage
    session.commit()
