"""
Shared request queue for MusicBrainz and Discogs HTTP calls.

All MB and Discogs requests must go through submit_lookup() / throttle() so
that consecutive outbound requests are spaced at least MIN_INTERVAL seconds
apart across all drives and lookup types, preventing 503 rate-limit errors
when several CDs are inserted simultaneously.

API
---
submit_lookup(key, fn, *args, **kwargs) -> bool
    Enqueue fn(*args, **kwargs) for serial execution.  key is a hashable
    dedup identifier, e.g. ("mb", disc_db_id) or ("discogs", disc_db_id).
    MB and Discogs keys are distinct so they don't block each other.
    Returns False without enqueueing if the same key is already in flight.

throttle() -> None
    Sleep until MIN_INTERVAL has elapsed since the last outbound HTTP call.
    Call this inside each lookup function immediately before each HTTP
    request (Discogs makes two calls per lookup, so throttle() runs twice).
"""

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

MIN_INTERVAL = 1.0  # seconds between consecutive outbound requests


class _LookupQueue:
    def __init__(self) -> None:
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._in_flight: set = set()
        self._lock = threading.Lock()
        self.last_request_at: float = 0.0
        self._thread: threading.Thread | None = None

    def submit(self, key, fn, args, kwargs) -> bool:
        with self._lock:
            if key in self._in_flight:
                return False
            self._in_flight.add(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="lookup-worker"
                )
                self._thread.start()
        self._q.put((key, fn, args, kwargs))
        return True

    def _run(self) -> None:
        while True:
            key, fn, args, kwargs = self._q.get()
            throttle()
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("Unhandled exception in lookup worker (key=%r)", key)
            finally:
                with self._lock:
                    self._in_flight.discard(key)


_queue = _LookupQueue()


def throttle() -> None:
    """Sleep until MIN_INTERVAL seconds have elapsed since the last outbound request."""
    gap = MIN_INTERVAL - (time.monotonic() - _queue.last_request_at)
    if gap > 0:
        time.sleep(gap)
    _queue.last_request_at = time.monotonic()


def submit_lookup(key, fn, *args, **kwargs) -> bool:
    """
    Enqueue fn(*args, **kwargs) on the shared lookup worker thread.
    Returns False without enqueuing if key is already in flight.
    """
    return _queue.submit(key, fn, args, kwargs)
