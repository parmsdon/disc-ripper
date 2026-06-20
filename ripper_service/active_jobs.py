"""
Shared in-memory registry of currently in-flight rip jobs.

job_starter.py spawns the background thread/subprocess for each running
rip job; job_rollback.py may need to forcibly kill that subprocess later
(ripping_enabled toggled off, or max_rippers decreased). Both need to see
the same tracking state, hence this shared module rather than a private
dict in job_starter.py.

Process-local only - lost on a ripper_service restart, same as any other
in-memory state. A restart orphans any real subprocess still running;
that's a pre-existing limitation, not something this module changes.
"""

import threading

_lock = threading.Lock()
_active = {}  # rip_job_id -> {"thread": Thread, "process": Popen|None}
_rolled_back = set()  # rip_job_id's deliberately killed by job_rollback.py


def register(rip_job_id, thread) -> None:
    with _lock:
        _active[rip_job_id] = {"thread": thread, "process": None}


def set_process(rip_job_id, process) -> None:
    with _lock:
        entry = _active.get(rip_job_id)
        if entry is not None:
            entry["process"] = process


def get_process(rip_job_id):
    with _lock:
        entry = _active.get(rip_job_id)
        return entry["process"] if entry else None


def mark_rolled_back(rip_job_id) -> None:
    """
    Flag a job as deliberately killed, so the original spawning thread's
    completion handler (which will see the killed subprocess exit and
    run_dvdbackup() return failure) knows to skip its own status update -
    job_rollback.py already set the authoritative final state.
    """
    with _lock:
        _rolled_back.add(rip_job_id)


def was_rolled_back(rip_job_id) -> bool:
    with _lock:
        return rip_job_id in _rolled_back


def unregister(rip_job_id) -> None:
    with _lock:
        _active.pop(rip_job_id, None)
        _rolled_back.discard(rip_job_id)
