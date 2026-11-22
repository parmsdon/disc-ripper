"""
Thread-safe tracker for in-flight cover art downloads.

discs.py registers downloads here; library.py checks before allowing generation.
"""
import threading
import time

_lock = threading.Lock()
_fetching: dict[int, float] = {}  # disc_id -> monotonic time when download started

# Downloads older than this are auto-expired (guard against crashes/leaks)
_MAX_AGE = 120.0


def _prune() -> None:
    """Remove stale entries (must be called under _lock)."""
    now = time.monotonic()
    stale = [k for k, t in _fetching.items() if now - t > _MAX_AGE]
    for k in stale:
        del _fetching[k]


def add(disc_id: int) -> None:
    with _lock:
        _fetching[disc_id] = time.monotonic()


def remove(disc_id: int) -> None:
    with _lock:
        _fetching.pop(disc_id, None)


def count() -> int:
    with _lock:
        _prune()
        return len(_fetching)
