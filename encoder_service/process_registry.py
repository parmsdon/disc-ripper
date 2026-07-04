"""
Thread-safe registry of active encode subprocesses.

Encoder workers register their subprocess before blocking on stdout and
deregister in a finally block. shutdown() calls terminate_all() so that
any in-flight HandBrakeCLI/flac/ffmpeg process is killed immediately,
letting the worker threads unblock and exit cleanly.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_procs: dict[int, object] = {}   # job_id → subprocess.Popen


def register(job_id: int, proc) -> None:
    with _lock:
        _procs[job_id] = proc


def deregister(job_id: int) -> None:
    with _lock:
        _procs.pop(job_id, None)


def _terminate(job_id: int) -> None:
    with _lock:
        proc = _procs.get(job_id)
    if proc is None:
        return
    try:
        proc.terminate()
        logger.info("Terminated subprocess for encode job %s", job_id)
    except Exception:
        logger.exception("Failed to terminate subprocess for encode job %s", job_id)


def kill_dvd(job_id: int) -> None:
    _terminate(job_id)


def kill_cd(job_id: int) -> None:
    _terminate(job_id)


def terminate_all() -> None:
    """Send SIGTERM to every registered subprocess."""
    with _lock:
        items = list(_procs.items())
    for job_id, _ in items:
        _terminate(job_id)
