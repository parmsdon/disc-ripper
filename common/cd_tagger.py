"""
CD file tagging and cover art embedding.

Called by library generation to write metadata into FLAC and MP3 files
in-place. Only modifies files whose tags differ from the desired values,
so repeat library generations are fast. Never touches audio data.
"""

import hashlib
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    ID3, ID3NoHeaderError,
    TIT2, TPE1, TALB, TPE2, TRCK, TPOS, TDRC, APIC,
)

log = logging.getLogger(__name__)

_CAA_URL = "https://coverartarchive.org/release/{mbid}/front-500"


def _cover_hash(data: bytes) -> bytes:
    return hashlib.md5(data, usedforsecurity=False).digest()
_CAA_UA = "discripper/1.0"
_JPEG_MIME = "image/jpeg"
_FRONT_COVER = 3  # ID3/FLAC picture type for front cover


def fetch_cover_from_url(disc_id: int, url: str, covers_dir: Path, force: bool = False) -> Optional[bytes]:
    """
    Return front-cover bytes, downloading from an arbitrary URL when not cached.
    Saves to covers_dir/<disc_id>.jpg. Returns None when unavailable.
    Pass force=True to re-download even if the file already exists.
    """
    covers_dir.mkdir(parents=True, exist_ok=True)
    dest = covers_dir / f"{disc_id}.jpg"
    if dest.exists() and not force:
        return dest.read_bytes()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _CAA_UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return data
    except Exception as e:
        log.warning("Cover art fetch failed for url %s: %s", url, e)
        return None


def fetch_cover(disc_id: int, mb_release_id: str, covers_dir: Path, force: bool = False) -> Optional[bytes]:
    """
    Return front-cover bytes, downloading from Cover Art Archive when not cached.
    Saves to covers_dir/<disc_id>.jpg. Returns None when unavailable.
    Pass force=True to re-download even if the file already exists (e.g. after re-identification).
    """
    covers_dir.mkdir(parents=True, exist_ok=True)
    dest = covers_dir / f"{disc_id}.jpg"
    if dest.exists() and not force:
        return dest.read_bytes()
    url = _CAA_URL.format(mbid=mb_release_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _CAA_UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return data
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.warning("CAA HTTP %s for release %s: %s", e.code, mb_release_id, e)
        return None
    except Exception as e:
        log.warning("Cover art fetch failed for release %s: %s", mb_release_id, e)
        return None


def tag_flac(path: Path, tags: dict, cover: Optional[bytes]) -> bool:
    """
    Write VorbisComment tags and optionally embed cover art in a FLAC file.
    Returns True if the file was modified.

    tags keys: title, artist, album, albumartist, tracknumber, tracktotal,
               discnumber (optional), disctotal (optional), year (optional).
    """
    audio = FLAC(str(path))
    changed = False

    desired: dict[str, str] = {
        "TITLE": tags.get("title") or "",
        "ARTIST": tags.get("artist") or "",
        "ALBUM": tags.get("album") or "",
        "ALBUMARTIST": tags.get("albumartist") or "",
        "TRACKNUMBER": str(tags["tracknumber"]),
        "TRACKTOTAL": str(tags["tracktotal"]),
    }
    if tags.get("discnumber"):
        desired["DISCNUMBER"] = str(tags["discnumber"])
        desired["DISCTOTAL"] = str(tags["disctotal"])
    if tags.get("year"):
        desired["DATE"] = str(tags["year"])

    for key, val in desired.items():
        # VorbisComment keys are case-insensitive; check both cases.
        current = audio.get(key) or audio.get(key.lower()) or []
        if current != [val]:
            audio[key] = val
            changed = True

    if cover is not None:
        existing = [p for p in audio.pictures if p.type == _FRONT_COVER]
        if not existing or _cover_hash(existing[0].data) != _cover_hash(cover):
            audio.clear_pictures()
            pic = Picture()
            pic.type = _FRONT_COVER
            pic.mime = _JPEG_MIME
            pic.data = cover
            audio.add_picture(pic)
            changed = True

    if changed:
        audio.save()
    return changed


def tag_mp3(path: Path, tags: dict, cover: Optional[bytes]) -> bool:
    """
    Write ID3 tags and optionally embed cover art in an MP3 file.
    Returns True if the file was modified.

    tags keys: same as tag_flac.
    """
    is_new = False
    try:
        audio = ID3(str(path))
    except ID3NoHeaderError:
        audio = ID3()
        is_new = True

    changed = False

    def _text(frame_id: str) -> str:
        f = audio.get(frame_id)
        # str() is required: TDRC stores ID3TimeStamp objects whose __eq__
        # does not compare equal to plain Python strings.
        return str(f.text[0]) if f and f.text else ""

    def _set(frame_id: str, cls, text: str) -> None:
        nonlocal changed
        if _text(frame_id) != text:
            audio[frame_id] = cls(encoding=3, text=text)
            changed = True

    track_str = f"{tags['tracknumber']}/{tags['tracktotal']}"
    disc_str = (
        f"{tags['discnumber']}/{tags['disctotal']}"
        if tags.get("discnumber")
        else None
    )

    _set("TIT2", TIT2, tags.get("title") or "")
    _set("TPE1", TPE1, tags.get("artist") or "")
    _set("TALB", TALB, tags.get("album") or "")
    _set("TPE2", TPE2, tags.get("albumartist") or "")
    _set("TRCK", TRCK, track_str)
    if disc_str:
        _set("TPOS", TPOS, disc_str)
    if tags.get("year"):
        _set("TDRC", TDRC, str(tags["year"]))

    if cover is not None:
        # APIC key includes the description field.
        existing = audio.get("APIC:cover") or audio.get("APIC:")
        if existing is None or _cover_hash(existing.data) != _cover_hash(cover):
            audio["APIC:cover"] = APIC(
                encoding=3,
                mime=_JPEG_MIME,
                type=_FRONT_COVER,
                desc="cover",
                data=cover,
            )
            changed = True

    if changed or is_new:
        audio.save(str(path), v2_version=3)
        return True
    return False
