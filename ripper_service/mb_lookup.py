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
import threading

logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)

import discid
import musicbrainzngs

from common.models import Disc, LookupCandidate
from ripper_service.discogs_lookup import lookup_discogs_for_disc

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


def _normalize_release(release: dict, mb_disc_id: str) -> dict:
    """
    Return the candidate_data dict stored in LookupCandidate for one MB release.

    Finds the specific medium (disc) within the release that contains mb_disc_id
    in its disc-list, so multi-disc albums show only the tracks for this physical
    disc. Falls back to the first medium if no match is found (shouldn't happen
    for results returned by a disc-ID lookup, but handled gracefully).

    NOTE: existing lookup_candidates rows stored before this fix (pre-0017) may
    have tracks from the first medium rather than the matched medium for multi-disc
    albums. To refresh, eject and reinsert the disc to trigger a new lookup.
    """
    artist = _artist_credit_string(release.get("artist-credit", []))
    date = release.get("date", "")
    year = date[:4] if date else ""

    medium_list = release.get("medium-list", [])
    medium_count = len(medium_list)

    # Find the medium whose disc-list contains our MB disc ID
    matched_medium = None
    for m in medium_list:
        if any(d.get("id") == mb_disc_id for d in m.get("disc-list", [])):
            matched_medium = m
            break

    medium = matched_medium if matched_medium is not None else (medium_list[0] if medium_list else {})
    medium_position = int(medium["position"]) if matched_medium is not None and medium.get("position") else None
    medium_title = medium.get("title") or None

    track_count = medium.get("track-count")
    tracks = []
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
        "medium_position": medium_position,
        "medium_count": medium_count or None,
        "medium_title": medium_title,
        "track_count": track_count,
        "tracks": tracks,
        "raw": release,
    }


def lookup_musicbrainz(
    disc_id: str,
    toc: str,
    disc_db_id: int,
    session_factory,
    discogs_token: str | None = None,
) -> None:
    """
    Background thread entry point. Queries MB, stores LookupCandidates, updates
    disc.mb_lookup_status. Opens its own DB session; caller must not pass one in.

    If discogs_token is provided and the MB result is a fuzzy multi-disc match
    with unknown disc position (medium_position=None, medium_count > 1), spawns
    a Discogs fallback lookup thread automatically.
    """
    session = session_factory()
    try:
        releases = _fetch_releases(disc_id, toc, disc_db_id)
        if releases is None:
            # _fetch_releases already handled the not_found or error case
            _set_status(session, disc_db_id, "error")
            return

        candidate_data_list = [_normalize_release(r, disc_id) for r in releases]

        for data in candidate_data_list:
            session.add(LookupCandidate(
                disc_id=disc_db_id,
                source="musicbrainz",
                selected=False,
                candidate_data=data,
            ))

        status = "found" if releases else "not_found"
        disc = session.get(Disc, disc_db_id)
        if disc:
            disc.mb_lookup_status = status
            # Set medium position/count/title from the first candidate that has a
            # confirmed disc-ID match (medium_position is None when no match was
            # found). All candidates for the same physical disc should have the
            # same position/count since they matched the same disc ID.
            if disc.mb_medium_position is None:
                for data in candidate_data_list:
                    if data.get("medium_position") is not None:
                        disc.mb_medium_position = data["medium_position"]
                        disc.mb_medium_count = data["medium_count"]
                        disc.mb_medium_title = data["medium_title"]
                        break
        # For fuzzy TOC matches the disc-ID loop above finds no medium_position,
        # leaving mb_medium_count unset. Pull it from the first candidate so the
        # Discogs fallback condition (mb_medium_count > 1) can fire.
        if disc and candidate_data_list:
            first = candidate_data_list[0]
            if disc.mb_medium_count is None and first.get("medium_count"):
                disc.mb_medium_count = first["medium_count"]
        session.commit()

        logger.info(
            "MusicBrainz lookup complete for disc #%d: %d release(s) (%s)",
            disc_db_id, len(releases), status,
        )

        # Trigger Discogs fallback if MB found results but couldn't pin the
        # disc position (fuzzy TOC match on a multi-disc set).
        if (
            discogs_token
            and status == "found"
            and disc is not None
            and disc.mb_medium_position is None
            and disc.mb_medium_count is not None
            and disc.mb_medium_count > 1
        ):
            mb_title = candidate_data_list[0].get("title", "") if candidate_data_list else ""
            if mb_title:
                logger.info(
                    "Triggering Discogs fallback lookup for disc #%d "
                    "(MB medium position unknown, %d-disc set)",
                    disc_db_id, disc.mb_medium_count,
                )
                threading.Thread(
                    target=lookup_discogs_for_disc,
                    args=(mb_title, disc.mb_medium_count, disc_db_id, discogs_token, session_factory),
                    daemon=True,
                ).start()

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
