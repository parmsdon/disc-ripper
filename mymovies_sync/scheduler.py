"""
My Movies sync scheduler.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m mymovies_sync.scheduler

Long-running background process (like ripper_service.main) - runs a
sync immediately on startup, then again every
cfg["mymovies"]["sync_interval_hours"] hours, logging the result each
time. Ctrl+C shuts it down cleanly. Not a cron job - keep it running
under tmux/screen or a process supervisor.
"""

import logging
import os
import time

from common.config import load_config
from mymovies_sync.sync import run_sync

logger = logging.getLogger(__name__)


def run(cfg: dict) -> None:
    interval_seconds = cfg["mymovies"]["sync_interval_hours"] * 3600

    while True:
        logger.info("Starting My Movies sync")
        try:
            result = run_sync(cfg)
            logger.info("My Movies sync finished: %s", result)
        except Exception:
            logger.exception("My Movies sync failed")

        logger.info("Next sync in %s hours", cfg["mymovies"]["sync_interval_hours"])
        time.sleep(interval_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    try:
        run(cfg)
    except KeyboardInterrupt:
        logger.info("My Movies sync scheduler stopping (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
