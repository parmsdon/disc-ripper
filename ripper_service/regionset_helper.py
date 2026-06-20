"""
regionset-based DVD region detection.

We only ever read, never set, the region - `regionset` prompts to change
it, so stdin is always fed "n". Output format varies slightly across
`regionset` builds, so parsing is best-effort and tolerant.

Drives can support more than one region (region-free or multi-region
drives report several digits, e.g. "Drive plays discs from this
region(s): 1 2 3 4 5 6 7 8"), so the parsed result is a space-separated
digit string, not a single integer.
"""

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# "RPC Phase: II" - colon-delimited form
_RPC_PHASE_COLON_RE = re.compile(r"rpc\s*phase\s*:\s*([A-Za-z0-9]+)", re.IGNORECASE)
# "...(RPC-II)" / "...(RPC II)" - parenthetical form, e.g. "Phase 2 (RPC-II)"
_RPC_PHASE_PAREN_RE = re.compile(r"\(\s*rpc[\s-]*([A-Za-z0-9]+)\s*\)", re.IGNORECASE)


def _extract_rpc_phase(output: str):
    # Colon and parenthetical forms are unambiguous; anything looser (e.g.
    # "Phase is 2") risks capturing filler words, so it's left as None.
    match = _RPC_PHASE_COLON_RE.search(output) or _RPC_PHASE_PAREN_RE.search(output)
    return match.group(1) if match else None


def _extract_regions(output: str):
    # Confirmed real-world phrasing: "Drive plays discs from this
    # region(s): 1 2 3 4 5 6 7 8" - space-separated, not comma-separated.
    # Prefer a line explicitly mentioning "region(s)"; fall back to any
    # line mentioning "region" at all, to tolerate minor wording variants.
    lines = output.splitlines()

    for line in lines:
        if "region(s)" in line.lower():
            digits = re.findall(r"\d+", line)
            if digits:
                return " ".join(digits)

    for line in lines:
        if "region" in line.lower():
            digits = re.findall(r"\d+", line)
            if digits:
                return " ".join(digits)

    return None


def read_region(device_path: str) -> dict:
    """
    Read the DVD region(s) supported by the drive at device_path via
    `regionset`.

    Returns:
        rpc_phase: str or None (e.g. "II")
        regions: str or None - space-separated region digits the drive
                 supports (e.g. "2" for a locked drive, "1 2 3 4 5 6 7 8"
                 for a region-free drive). None if unparseable -
                 raw_output has the detail for manual review.
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
        return {"rpc_phase": None, "regions": None, "raw_output": message, "error": True}

    output = proc.stdout or ""

    if proc.returncode != 0:
        message = output.strip() or proc.stderr.strip() or f"regionset exited {proc.returncode}"
        logger.warning("regionset failed for %s: %s", device_path, message)
        return {"rpc_phase": None, "regions": None, "raw_output": message, "error": True}

    rpc_phase = _extract_rpc_phase(output)
    regions = _extract_regions(output)

    return {"rpc_phase": rpc_phase, "regions": regions, "raw_output": output, "error": False}
