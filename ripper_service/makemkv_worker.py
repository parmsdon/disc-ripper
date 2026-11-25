"""
MakeMKV disc backup worker.

Provides two functions used by job_starter.py:

  find_disc_index(device_path) -> int | None
      Runs "makemkvcon -r info disc:9999" and returns the drive index for
      device_path (after resolving symlinks). Returns None on any error.

  run_makemkv(device_path, iso_path, rip_job_id, session_factory,
              timeout_seconds=7200) -> dict
      Runs "makemkvcon -r --progress=-same backup --decrypt disc:N iso_path".
      Returns {"success": bool, "log": str, "return_code": int | None,
               "dirty": bool}.  Never raises.

Progress protocol (-r --progress=-same):
  PRGT:<code>,<n>,"<task>"    main task (ignored - too coarse)
  PRGC:<code>,<n>,"<step>"    current step label
  PRGV:<step>,<overall>,<max> overall progress; max is always 65536
  MSG:<code>,<flags>,<cnt>,"<text>","<fmt>",... informational/error messages

Dirty detection: a MSG line whose text contains "Failed to read" but does
NOT also contain "AUTHENTICATION", "work around", or "scrambled" is a
genuine unrecoverable sector read.  Code 4004 / "attempting to work around"
and "READ OF SCRAMBLED SECTOR WITHOUT AUTHENTICATION" are expected for
CSS-protected discs and do not indicate a dirty rip.
"""

import logging
import os
import re
import subprocess
import threading
import time

from common.models import RipJob
from ripper_service import active_jobs

logger = logging.getLogger(__name__)

_FIND_INDEX_TIMEOUT = 30  # seconds

# Progress / message patterns
_PRGC_RE = re.compile(r'^PRGC:\d+,\d+,"([^"]*)"')
_PRGV_RE = re.compile(r'^PRGV:(\d+),(\d+),(\d+)')
_MSG_RE  = re.compile(r'^MSG:\d+,\d+,\d+,"([^"]*)"')

# Dirty detection
_MKV_DIRTY_RE    = re.compile(r'Failed to read', re.IGNORECASE)
_MKV_EXPECTED_RE = re.compile(r'(AUTHENTICATION|work.{0,5}around|scrambled)', re.IGNORECASE)

_PROGRESS_THROTTLE_SECONDS = 3.0
_MKV_BACKUP_FAILED_RE = re.compile(r'Backup failed', re.IGNORECASE)


def detect_dirty_makemkv(log_text: str) -> bool:
    for line in log_text.splitlines():
        if _MKV_DIRTY_RE.search(line) and not _MKV_EXPECTED_RE.search(line):
            return True
    return False


def find_disc_index(device_path: str) -> int | None:
    """
    Return the makemkvcon drive index for device_path (realpath resolved).
    Runs "makemkvcon -r info disc:9999" and parses DRV lines.
    DRV format: DRV:<idx>,<type>,<flags>,<drive_flag>,"<label>","<vol_id>","<device>"
    Returns None on any error or if the device is not found.
    """
    try:
        real_dev = subprocess.check_output(
            ["realpath", device_path], text=True, timeout=5,
        ).strip()
    except Exception as exc:
        logger.warning("find_disc_index: realpath(%s) failed: %s", device_path, exc)
        return None

    try:
        result = subprocess.run(
            ["makemkvcon", "-r", "info", "disc:9999"],
            capture_output=True, text=True, timeout=_FIND_INDEX_TIMEOUT,
        )
        output = result.stdout + result.stderr
    except FileNotFoundError:
        logger.error("makemkvcon not found — is MakeMKV installed?")
        return None
    except subprocess.TimeoutExpired:
        logger.error("makemkvcon info timed out after %ds", _FIND_INDEX_TIMEOUT)
        return None
    except Exception as exc:
        logger.error("makemkvcon info failed: %s", exc)
        return None

    for line in output.splitlines():
        if not line.startswith("DRV:"):
            continue
        # DRV:<idx>,<type>,<flags>,<drive_flag>,"<label>","<vol_id>","<device>"
        # Split on comma, strip quotes from string fields.
        parts = line[4:].split(",")
        if len(parts) < 7:
            continue
        idx_str = parts[0]
        dev_field = parts[6].strip().strip('"')
        if dev_field == real_dev:
            try:
                return int(idx_str)
            except ValueError:
                logger.warning("Could not parse DRV index from %r", line)
                return None

    logger.warning(
        "find_disc_index: device %s (real: %s) not found in DRV output",
        device_path, real_dev,
    )
    return None


def run_makemkv(
    device_path: str,
    iso_path: str,
    rip_job_id: int,
    session_factory,
    timeout_seconds: int = 7200,
) -> dict:
    """
    Run MakeMKV backup to iso_path.  Returns:
      {"success": bool, "log": str, "return_code": int | None, "dirty": bool}
    Never raises.
    """
    idx = find_disc_index(device_path)
    if idx is None:
        msg = f"Could not find device {device_path} in makemkvcon DRV list"
        logger.error("run_makemkv: %s", msg)
        return {"success": False, "log": msg, "return_code": None, "dirty": False}

    try:
        os.makedirs(os.path.dirname(iso_path), exist_ok=True)
    except OSError as exc:
        msg = f"Cannot create output directory for {iso_path}: {exc}"
        logger.error("run_makemkv: %s", msg)
        return {"success": False, "log": msg, "return_code": None, "dirty": False}

    tmp_path = iso_path + ".tmp"
    command = [
        "makemkvcon", "-r", "--progress=-same",
        "backup", "--decrypt", f"disc:{idx}", tmp_path,
    ]

    log_lines: list[str] = []
    session = session_factory()
    current_stage = "Starting MakeMKV"
    backup_failed = False
    timed_out = False
    last_progress_write = 0.0

    try:
        if active_jobs.was_rolled_back(rip_job_id):
            return {"success": False, "log": "Rolled back before starting", "return_code": None, "dirty": False}

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        active_jobs.set_process(rip_job_id, proc)

        def _kill_on_timeout():
            nonlocal timed_out
            timed_out = True
            logger.warning(
                "makemkvcon timed out after %ds for rip_job %s — killing",
                timeout_seconds, rip_job_id,
            )
            try:
                proc.kill()
            except Exception:
                pass

        timer = threading.Timer(timeout_seconds, _kill_on_timeout)
        timer.start()

        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                log_lines.append(line)

                if _MKV_BACKUP_FAILED_RE.search(line):
                    backup_failed = True

                m = _PRGC_RE.match(line)
                if m:
                    new_stage = m.group(1)
                    if new_stage != current_stage:
                        current_stage = new_stage
                        # Write stage immediately on change so post-copy
                        # finalization stages (e.g. "Saving backup result")
                        # appear without waiting for the next PRGV line.
                        rip_job = session.get(RipJob, rip_job_id)
                        if rip_job is not None:
                            rip_job.progress_stage = (
                                f"MakeMKV: {current_stage}" if current_stage else "MakeMKV"
                            )
                            session.commit()
                        last_progress_write = time.time()
                    continue

                m = _PRGV_RE.match(line)
                if m:
                    step_val = int(m.group(1))
                    overall = int(m.group(2))
                    max_val = int(m.group(3))
                    # Pre-copy stages (scanning, reading) leave overall near 0.
                    # Use per-step progress until copying starts; switch to
                    # overall once the stage name signals active file copy.
                    is_copying = current_stage.lower().startswith("copying")
                    if is_copying and max_val > 0:
                        pct = int(overall / max_val * 100)
                    else:
                        pct = int(step_val / 65536 * 100)
                    now = time.time()
                    if now - last_progress_write >= _PROGRESS_THROTTLE_SECONDS:
                        rip_job = session.get(RipJob, rip_job_id)
                        if rip_job is not None:
                            rip_job.progress_percent = pct
                            rip_job.progress_stage = (
                                f"MakeMKV: {current_stage}" if current_stage else "MakeMKV"
                            )
                            session.commit()
                        last_progress_write = now
                    continue

                # MSG lines captured via log_lines; no extra action needed.
        finally:
            timer.cancel()

        proc.wait()

        if timed_out:
            log_lines.append(f"[timeout] makemkvcon killed after {timeout_seconds}s")

        full_log = "\n".join(log_lines)

        success = (
            proc.returncode == 0
            and not backup_failed
            and not timed_out
            and os.path.exists(tmp_path)
            and os.path.getsize(tmp_path) > 0
        )

        if success:
            os.rename(tmp_path, iso_path)
            dirty = detect_dirty_makemkv(full_log)
        else:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            dirty = False

        rip_job = session.get(RipJob, rip_job_id)
        if rip_job is not None:
            existing = rip_job.log or ""
            rip_job.log = (existing + "\n\n--- MakeMKV ---\n" if existing else "") + full_log
            session.commit()

        return {"success": success, "log": full_log, "return_code": proc.returncode, "dirty": dirty}

    except Exception as exc:
        logger.exception("run_makemkv crashed for rip_job %s", rip_job_id)
        full_log = "\n".join(log_lines) + f"\n\nException: {exc}"
        try:
            rip_job = session.get(RipJob, rip_job_id)
            if rip_job is not None:
                existing = rip_job.log or ""
                rip_job.log = (existing + "\n\n--- MakeMKV ---\n" if existing else "") + full_log
                session.commit()
        except Exception:
            logger.exception("Failed to persist crash log for rip_job %s", rip_job_id)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return {"success": False, "log": full_log, "return_code": None, "dirty": False}

    finally:
        session.close()
