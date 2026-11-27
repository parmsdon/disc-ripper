"""
My Movies one-off sync runner.

Run with (from the project root, venv active):
    DISCRIPPER_ENV=dev python3 -m mymovies_sync.scheduler

Runs a single sync and exits. Scheduled/periodic sync has been removed;
sync now runs only when triggered via the web UI (Sync Now button).
This module is kept as a convenience wrapper for shell-triggered syncs.
The direct equivalent is: python3 -m mymovies_sync.sync
"""

import logging
import os

from common.config import load_config
from mymovies_sync.sync import run_sync

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config(os.environ.get("DISCRIPPER_ENV"))
    logger.info("Starting My Movies sync")
    try:
        result = run_sync(cfg)
        logger.info("My Movies sync finished: %s", result)
    except Exception:
        logger.exception("My Movies sync failed")


if __name__ == "__main__":
    main()
