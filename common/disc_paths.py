"""Shared helpers for per-disc filesystem path construction."""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def encode_dir_name(temp_name: str | None, disc_id: int) -> str:
    """Return the directory name used for a disc's encode outputs.

    Format: <temp_name>_<disc_id>  or  disc_<disc_id> when temp_name is absent.
    The disc_id suffix makes every output directory unique even when two discs
    share the same working title (e.g. a re-rip).
    """
    if temp_name:
        return f"{temp_name}_{disc_id}"
    return f"disc_{disc_id}"


def sanitize_iso_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9]', '_', name).strip('_').upper() or "unnamed"


def rename_iso_at_lock(disc_id: int, temp_name: str, raw_path: str, datastore_root: str) -> None:
    """Rename the single ISO in raw/<disc_id>/ to the sanitised temp_name.

    Called when name_confirmed transitions to True on a completed disc:
    either from update_temp_name (case a: user confirms after rip) or from
    _finish_success/_finish_success_makemkv (case b: rip completes with the
    name already confirmed).  Safe to call multiple times — a no-op if the
    target filename already matches.
    """
    if not temp_name or not raw_path or not datastore_root:
        return
    raw_dir = Path(datastore_root) / raw_path
    if not raw_dir.is_dir():
        return
    isos = list(raw_dir.glob("*.iso"))
    if len(isos) != 1:
        logger.warning(
            "ISO rename skipped for disc #%s: found %d ISO(s) in %s",
            disc_id, len(isos), raw_dir,
        )
        return
    new_name = sanitize_iso_name(temp_name)
    new_path = raw_dir / f"{new_name}.iso"
    if isos[0] == new_path:
        return
    try:
        isos[0].rename(new_path)
        logger.info("Renamed ISO for disc #%s: %s → %s", disc_id, isos[0].name, new_path.name)
    except OSError as exc:
        logger.warning("ISO rename failed for disc #%s: %s", disc_id, exc)
