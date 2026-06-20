"""
regionset-based DVD region detection.

We only ever read, never set, the region - `regionset` prompts to change
it, so stdin is always fed "n". Output format varies slightly across
`regionset` builds, so parsing is best-effort and tolerant; ambiguous
output (multiple regions, unset/mask, unparseable) is left as None for a
human to review via raw_output rather than guessed at.
"""

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# "RPC Phase: II" - colon-delimited form
_RPC_PHASE_COLON_RE = re.compile(r"rpc\s*phase\s*:\s*([A-Za-z0-9]+)", re.IGNORECASE)
# "...(RPC-II)" / "...(RPC II)" - parenthetical form, e.g. "Phase 2 (RPC-II)"
_RPC_PHASE_PAREN_RE = re.compile(r"\(\s*rpc[\s-]*([A-Za-z0-9]+)\s*\)", re.IGNORECASE)

_REGION_LINE_RE = re.compile(r"region\(?s?\)?[:\s]+([0-9][0-9,\s]*)", re.IGNORECASE)


def _extract_rpc_phase(output: str):
    # Colon and parenthetical forms are unambiguous; anything looser (e.g.
    # "Phase is 2") risks capturing filler words, so it's left as None.
    match = _RPC_PHASE_COLON_RE.search(output) or _RPC_PHASE_PAREN_RE.search(output)
    return match.group(1) if match else None


def read_region(device_path: str) -> dict:
    """
    Read the DVD region of the drive at device_path via `regionset`.

    Returns:
        rpc_phase: str or None (e.g. "II")
        region: int or None (None if unset, multiple regions, or
                unparseable - raw_output has the detail for manual review)
        raw_output: full stdout (or an error message if the command failed)
        error: True if the command failed to run or exited nonzero

    Never raises.
    """
    try:
        proc = subprocess.run(
            ["regionset", device_path],
            input="n\n",
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        message = f"Failed to run regionset for {device_path}: {exc}"
        logger.warning(message)
        return {"rpc_phase": None, "region": None, "raw_output": message, "error": True}

    output = proc.stdout or ""

    if proc.returncode != 0:
        message = output.strip() or proc.stderr.strip() or f"regionset exited {proc.returncode}"
        logger.warning("regionset failed for %s: %s", device_path, message)
        return {"rpc_phase": None, "region": None, "raw_output": message, "error": True}

    rpc_phase = _extract_rpc_phase(output)

    region = None
    region_match = _REGION_LINE_RE.search(output)
    if region_match:
        numbers = re.findall(r"\d+", region_match.group(1))
        if len(numbers) == 1:
            region = int(numbers[0])
        # Multiple numbers (multi-region disc) or none parsed -> leave
        # region=None; raw_output carries the detail for manual review.

    return {"rpc_phase": rpc_phase, "region": region, "raw_output": output, "error": False}
