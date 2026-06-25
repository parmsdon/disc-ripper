"""
MusicBrainz disc ID computation and lookup for CD rips.

compute_mb_disc_id(device_path) -> dict
    Reads the TOC from the optical drive via libdiscid and computes the
    MusicBrainz disc ID. Returns {"disc_id": str, "toc": str, "track_count":
    int} on success, or {"disc_id": None, "toc": None, "error": str} on
    failure. Never raises.

lookup_musicbrainz(disc_id, toc, disc_db_id, session_factory) -> None
    Runs in a background thread. Queries MusicBrainz for releases matching
    disc_id (falling back to fuzzy TOC match via toc), stores each result as
    a LookupCandidate row, then sets disc.mb_lookup_status to "found" /
    "not_found" / "error". musicbrainzngs enforces the 1 req/sec rate limit
    automatically once set_useragent is called; no extra sleep needed for a
    single lookup (only relevant for future batch operations).
"""

import logging

import discid
import musicbrainzngs

from common.models import Disc, LookupCandidate

logger = logging.getLogger(__name__)

musicbrainzngs.set_useragent(
    "DiscRipper", "0.1",
    "https://github.com/parmsdon/disc-ripper",
)


def compute_mb_disc_id(device_path: str) -> dict:
    """Read TOC from device_path and compute the MusicBrainz disc ID. Never raises."""
    try:
        disc = discid.read(device_path)
        return {
            "disc_id": disc.id,
            "toc": disc.toc_string,
            "track_count": len(disc.tracks),
        }
    except discid.DiscError as e:
        return {"disc_id": None, "toc": None, "error": f"DiscError: {e}"}
    except Exception as e:
        return {"disc_id": None, "toc": None, "error": str(e)}


def _artist_credit_string(credit_list: list) -> str:
    """Join a MusicBrainz artist-credit list into a display name string."""
    if not credit_list:
        return ""
    parts = []
    for item in credit_list:
        if isinstance(item, dict):
            parts.append(item.get("name") or item.get("artist", {}).get("name", ""))
            if item.get("joinphrase"):
                parts.append(item["joinphrase"])
    return "".join(parts).strip()


def _normalize_release(release: dict) -> dict:
    """Return the candidate_data dict stored in LookupCandidate for one MB release."""
    artist = _artist_credit_string(release.get("artist-credit", []))
    date = release.get("date", "")
    year = date[:4] if date else ""

    track_count = None
    tracks = []
    medium_list = release.get("medium-list", [])
    if medium_list:
        medium = medium_list[0]  # first medium = standard single-disc release
        track_count = medium.get("track-count")
        for track in medium.get("track-list", []):
            recording = track.get("recording", {})
            track_artist = _artist_credit_string(
                track.get("artist-credit") or recording.get("artist-credit", [])
            )
            tracks.append({
                "number": track.get("number") or track.get("position"),
                "title": recording.get("title") or track.get("title", ""),
                "artist": track_artist,
            })

    return {
        "mb_release_id": release["id"],
        "title": release.get("title", ""),
        "artist": artist,
        "year": year,
        "track_count": track_count,
        "tracks": tracks,
        "raw": release,
    }


def lookup_musicbrainz(
    disc_id: str,
    toc: str,
    disc_db_id: int,
    session_factory,
) -> None:
    """
    Background thread entry point. Queries MB, stores LookupCandidates, updates
    disc.mb_lookup_status. Opens its own DB session; caller must not pass one in.
    """
    session = session_factory()
    try:
        releases = _fetch_releases(disc_id, toc, disc_db_id)
        if releases is None:
            # _fetch_releases already handled the not_found or error case
            _set_status(session, disc_db_id, "error")
            return

        for release in releases:
            session.add(LookupCandidate(
                disc_id=disc_db_id,
                source="musicbrainz",
                selected=False,
                candidate_data=_normalize_release(release),
            ))

        status = "found" if releases else "not_found"
        disc = session.get(Disc, disc_db_id)
        if disc:
            disc.mb_lookup_status = status
        session.commit()

        logger.info(
            "MusicBrainz lookup complete for disc #%d: %d release(s) (%s)",
            disc_db_id, len(releases), status,
        )

    except Exception:
        logger.exception("MusicBrainz lookup failed for disc #%d", disc_db_id)
        try:
            _set_status(session, disc_db_id, "error")
        except Exception:
            pass
    finally:
        session.close()


def _fetch_releases(disc_id: str, toc: str, disc_db_id: int):
    """
    Call the MB web service. Returns a list of release dicts (may be empty),
    or None on network/server error (caller sets status="error").
    On clean 404 (no results), returns [] so caller sets "not_found".
    """
    try:
        result = musicbrainzngs.get_releases_by_discid(
            disc_id,
            toc=toc,
            includes=["recordings", "artist-credits"],
            cdstubs=False,
        )
    except musicbrainzngs.ResponseError as e:
        cause = getattr(e, "cause", None)
        if cause is not None and getattr(cause, "code", None) == 404:
            return []
        logger.error("MB ResponseError for disc #%d: %s", disc_db_id, e)
        return None
    except musicbrainzngs.NetworkError as e:
        logger.error("MB NetworkError for disc #%d: %s", disc_db_id, e)
        return None

    return (
        result.get("disc", {}).get("release-list")
        or result.get("release-list")
        or []
    )


def _set_status(session, disc_db_id: int, status: str) -> None:
    disc = session.get(Disc, disc_db_id)
    if disc:
        disc.mb_lookup_status = status
        session.commit()
