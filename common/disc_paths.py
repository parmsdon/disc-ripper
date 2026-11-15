"""Shared helpers for per-disc filesystem path construction."""


def encode_dir_name(temp_name: str | None, disc_id: int) -> str:
    """Return the directory name used for a disc's encode outputs.

    Format: <temp_name>_<disc_id>  or  disc_<disc_id> when temp_name is absent.
    The disc_id suffix makes every output directory unique even when two discs
    share the same working title (e.g. a re-rip).
    """
    if temp_name:
        return f"{temp_name}_{disc_id}"
    return f"disc_{disc_id}"
