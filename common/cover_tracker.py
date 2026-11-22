"""
Cross-worker tracker for in-flight cover art downloads.

Uses marker files (covers/<disc_id>.downloading) so all gunicorn workers
and processes see the same state via the shared NFS datastore.

Marker files are created with touch() when a download starts and unlinked
when it finishes. Any marker older than _MAX_AGE seconds is treated as stale
(guards against worker crashes that never called remove()).
"""
import time
from pathlib import Path

_MAX_AGE = 120.0  # seconds


def add(disc_id: int, covers_dir: Path) -> None:
    covers_dir.mkdir(parents=True, exist_ok=True)
    (covers_dir / f"{disc_id}.downloading").touch()


def remove(disc_id: int, covers_dir: Path) -> None:
    try:
        (covers_dir / f"{disc_id}.downloading").unlink(missing_ok=True)
    except OSError:
        pass


def count(covers_dir: Path) -> int:
    """Return number of non-stale in-flight downloads (visible to all workers)."""
    now = time.time()
    n = 0
    try:
        for p in covers_dir.glob("*.downloading"):
            try:
                if now - p.stat().st_mtime <= _MAX_AGE:
                    n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n
