"""
identify_cd cover-download branching — four cases plus error isolation.

Cases:
  1. cover_candidate_id → MB candidate      → fetch_cover called with candidate_data["mb_release_id"]
  2. cover_candidate_id → Discogs candidate → fetch_cover_from_url called with candidate_data["image_url"]
  3. cover_candidate_id absent, disc has mb_release_id  → fetch_cover called with disc.mb_release_id
  3b. cover_candidate_id absent, Discogs selected        → fetch_cover_from_url with selected candidate's image_url
  4. No cover source at all                 → no thread started, 200 returned
  5. Exception in cover section             → still 200, identification already committed
"""
from unittest.mock import MagicMock, patch
import pytest
from flask import Flask

from common.models import DiscType
from api.routes.discs import discs_bp


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    a = Flask(__name__)
    a.config["DISCRIPPER_CFG"] = {"storage": {"datastore_root": "/fake"}}
    a.register_blueprint(discs_bp, url_prefix="/api/discs")
    return a


def _session(disc):
    s = MagicMock()
    s.get.return_value = disc
    return s


def _disc(mb_release_id=None, candidates=None):
    d = MagicMock()
    d.id = 42
    d.type = DiscType.cd
    d.mb_release_id = mb_release_id
    d.lookup_candidates = candidates or []
    d.temp_name = "An Album"
    d.mb_medium_count = None
    d.mb_medium_position = None
    return d


def _mb_cand(cid, mbid):
    c = MagicMock()
    c.id = cid
    c.source = "musicbrainz"
    c.candidate_data = {"mb_release_id": mbid}
    c.selected = False
    return c


def _discogs_cand(cid, image_url):
    c = MagicMock()
    c.id = cid
    c.source = "discogs"
    c.candidate_data = {"image_url": image_url, "medium_position": 1, "medium_count": 1}
    c.selected = False
    return c


BASE = {"album_title": "An Album", "album_artist": "An Artist", "tracks": []}


def _patch():
    """Context manager: patch threading.Thread so no real threads start."""
    return patch("threading.Thread")


# ── helpers ──────────────────────────────────────────────────────────────────

def _thread_dl_args(mock_thread):
    """Return the args tuple passed to the first Thread call (disc_id, X, covers_dir)."""
    return mock_thread.call_args_list[0].kwargs["args"]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_cover_locked_mb_candidate(app):
    """Case 1: cover locked to an MB candidate → uses that candidate's mb_release_id."""
    cand = _mb_cand(cid=10, mbid="release-abc-123")
    disc = _disc(candidates=[cand])
    session = _session(disc)
    app.config["DB_SESSION"] = lambda: session

    with _patch() as mock_thread, app.test_client() as c:
        resp = c.patch("/api/discs/42/identify-cd", json={
            **BASE,
            "selected_candidate_id": 10,
            "cover_candidate_id": 10,
        })

    assert resp.status_code == 200
    assert mock_thread.call_count == 1
    assert _thread_dl_args(mock_thread)[1] == "release-abc-123"


def test_cover_locked_discogs_candidate(app):
    """Case 2: cover locked to a Discogs candidate → uses that candidate's image_url."""
    cand = _discogs_cand(cid=20, image_url="https://img.discogs.com/cover.jpg")
    disc = _disc(candidates=[cand])
    session = _session(disc)
    app.config["DB_SESSION"] = lambda: session

    with _patch() as mock_thread, app.test_client() as c:
        resp = c.patch("/api/discs/42/identify-cd", json={
            **BASE,
            "selected_candidate_id": 20,
            "cover_candidate_id": 20,
        })

    assert resp.status_code == 200
    assert mock_thread.call_count == 1
    assert _thread_dl_args(mock_thread)[1] == "https://img.discogs.com/cover.jpg"


def test_cover_unlocked_uses_disc_mb_release_id(app):
    """Case 3: no cover lock, disc has mb_release_id → uses disc.mb_release_id."""
    disc = _disc(mb_release_id="disc-mb-id-999")
    session = _session(disc)
    app.config["DB_SESSION"] = lambda: session

    with _patch() as mock_thread, app.test_client() as c:
        resp = c.patch("/api/discs/42/identify-cd", json={
            **BASE,
            "mb_release_id": "disc-mb-id-999",
        })

    assert resp.status_code == 200
    assert mock_thread.call_count == 1
    assert _thread_dl_args(mock_thread)[1] == "disc-mb-id-999"


def test_cover_unlocked_discogs_selected_uses_image_url(app):
    """Case 3b: no cover lock, Discogs candidate selected → uses selected candidate's image_url."""
    cand = _discogs_cand(cid=30, image_url="https://img.discogs.com/other.jpg")
    disc = _disc(mb_release_id=None, candidates=[cand])
    session = _session(disc)
    app.config["DB_SESSION"] = lambda: session

    with _patch() as mock_thread, app.test_client() as c:
        resp = c.patch("/api/discs/42/identify-cd", json={
            **BASE,
            "selected_candidate_id": 30,
        })

    assert resp.status_code == 200
    assert mock_thread.call_count == 1
    assert _thread_dl_args(mock_thread)[1] == "https://img.discogs.com/other.jpg"


def test_no_cover_source_no_thread(app):
    """Case 4: no mb_release_id, no candidates → no thread started, still 200."""
    disc = _disc(mb_release_id=None)
    session = _session(disc)
    app.config["DB_SESSION"] = lambda: session

    with _patch() as mock_thread, app.test_client() as c:
        resp = c.patch("/api/discs/42/identify-cd", json=BASE)

    assert resp.status_code == 200
    assert mock_thread.call_count == 0


def test_cover_error_does_not_fail_confirm(app):
    """Case 5: exception in cover section → still 200, identification already committed."""
    disc = _disc(mb_release_id="some-mbid")
    session = _session(disc)
    app.config["DB_SESSION"] = lambda: session

    with patch("threading.Thread", side_effect=RuntimeError("thread boom")), \
         app.test_client() as c:
        resp = c.patch("/api/discs/42/identify-cd", json={
            **BASE,
            "mb_release_id": "some-mbid",
        })

    assert resp.status_code == 200
    session.commit.assert_called_once()
