"""
Discogs release lookup — fallback for multi-disc CDs where MusicBrainz
returns a fuzzy match without a confirmed disc position (medium_position=NULL,
medium_count > 1).

Public entry point: lookup_discogs_for_disc() — same thread-based pattern
as lookup_musicbrainz; caller spawns a daemon thread, never awaits a result.
"""

import logging

import requests

from common.models import LookupCandidate

logger = logging.getLogger(__name__)

DISCOGS_API_BASE = "https://api.discogs.com"
USER_AGENT = "DiscRipper/0.1 +https://github.com/parmsdon/disc-ripper"


def search_discogs(title: str, token: str) -> list[dict]:
    """
    Search Discogs for CD releases matching title.
    Returns list of raw result dicts or [] on any error. Never raises.
    """
    try:
        resp = requests.get(
            f"{DISCOGS_API_BASE}/database/search",
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": f"Discogs token={token}",
            },
            params={"q": title, "type": "release", "format": "CD"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception:
        logger.warning("Discogs search failed for %r", title, exc_info=True)
        return []


def get_release(release_id: int, token: str) -> dict | None:
    """
    Fetch full release details from Discogs.
    Returns the release dict or None on any error. Never raises.
    """
    try:
        resp = requests.get(
            f"{DISCOGS_API_BASE}/releases/{release_id}",
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": f"Discogs token={token}",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.warning("Discogs get_release failed for release_id=%s", release_id, exc_info=True)
        return None


def _join_artists(artists: list[dict]) -> str:
    """Join a Discogs artist list into a display name string."""
    if not artists:
        return ""
    parts = []
    for a in artists:
        parts.append(a.get("name", ""))
        join = a.get("join", "")
        if join and join.strip() not in (",", ""):
            parts.append(f" {join.strip()} ")
        elif join.strip() == ",":
            parts.append(", ")
    return "".join(parts).strip().rstrip(",").strip()


def normalize_discogs_release(release: dict, mb_medium_count: int) -> list[dict]:
    """
    Parse a full Discogs release dict into one candidate dict per disc in the set.

    Discogs multi-disc tracklists use "N-T" positions (e.g. "1-1", "2-3").
    Single-disc releases use plain integers — these are skipped (not our use case).

    Returns a list of candidate dicts (one per disc), or [] if the release
    doesn't have a multi-disc "N-T" tracklist.
    """
    tracklist = release.get("tracklist", [])
    release_artist = _join_artists(release.get("artists", []))

    # Group tracks by disc number, skipping entries without "N-T" positions.
    discs: dict[int, list[dict]] = {}
    for track in tracklist:
        position = track.get("position", "")
        if "-" not in position:
            continue
        parts = position.split("-", 1)
        try:
            disc_num = int(parts[0])
            track_num = int(parts[1])
        except ValueError:
            continue
        track_artist = _join_artists(track.get("artists", [])) or release_artist
        discs.setdefault(disc_num, []).append({
            "_num": track_num,
            "number": str(track_num),
            "title": track.get("title", ""),
            "artist": track_artist,
        })

    if not discs:
        return []  # No "N-T" positions found — single disc or unrecognised format

    total_discs = max(discs.keys())
    candidates = []
    for disc_num in range(1, total_discs + 1):
        raw_tracks = discs.get(disc_num, [])
        if not raw_tracks:
            continue
        tracks = [
            {"number": t["number"], "title": t["title"], "artist": t["artist"]}
            for t in sorted(raw_tracks, key=lambda t: t["_num"])
        ]
        candidates.append({
            "discogs_release_id": release["id"],
            "title": release.get("title", ""),
            "artist": release_artist,
            "year": str(release.get("year", "")),
            "medium_position": disc_num,
            "medium_count": total_discs,
            "track_count": len(tracks),
            "tracks": tracks,
        })

    return candidates


def lookup_discogs_for_disc(
    mb_title: str,
    mb_medium_count: int,
    disc_db_id: int,
    token: str,
    session_factory,
) -> None:
    """
    Background thread entry point. Searches Discogs for mb_title, fetches the
    first CD result's full details, normalizes it into one LookupCandidate per
    disc in the set, and stores them in the DB.
    """
    session = session_factory()
    try:
        results = search_discogs(mb_title, token)
        if not results:
            logger.info("Discogs: no results for disc #%d (%r)", disc_db_id, mb_title)
            return

        # Pick the first result whose format list includes "CD".
        chosen = None
        for r in results:
            formats = r.get("format") or []
            if any("CD" in str(f) for f in formats):
                chosen = r
                break

        if chosen is None:
            logger.info("Discogs: no CD-format result for disc #%d", disc_db_id)
            return

        release = get_release(chosen["id"], token)
        if release is None:
            return

        candidates = normalize_discogs_release(release, mb_medium_count)
        if not candidates:
            logger.info(
                "Discogs release %s has no multi-disc tracklist for disc #%d",
                chosen["id"], disc_db_id,
            )
            return

        for data in candidates:
            session.add(LookupCandidate(
                disc_id=disc_db_id,
                source="discogs",
                selected=False,
                candidate_data=data,
            ))

        session.commit()
        logger.info(
            "Discogs lookup complete for disc #%d: %d disc candidate(s) added",
            disc_db_id, len(candidates),
        )

    except Exception:
        logger.warning("Discogs lookup failed for disc #%d", disc_db_id, exc_info=True)
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()
