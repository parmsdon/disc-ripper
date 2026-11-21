import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export default function CdIdentifyPanel({ disc, onConfirm, onSkip }) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [physicalTracks, setPhysicalTracks] = useState([]);

  const [albumTitle, setAlbumTitle] = useState("");
  const [albumArtist, setAlbumArtist] = useState("");
  const [trackTitles, setTrackTitles] = useState(new Map());
  const [trackArtists, setTrackArtists] = useState(new Map());
  const [showArtistCol, setShowArtistCol] = useState(false);

  const [selectedCandidateId, setSelectedCandidateId] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState(null);

  function applyCandidate(cand, tracks) {
    setAlbumTitle(cand.title || "");
    const artist = cand.artist || "";
    setAlbumArtist(artist);
    setSelectedCandidateId(cand.id);
    const titles = new Map();
    const artists = new Map();
    for (const track of tracks) {
      const mbTrack = cand.tracks?.find(
        (t) => parseInt(t.number, 10) === track.track_number
      );
      titles.set(track.id, mbTrack?.title || "");
      artists.set(track.id, mbTrack?.artist || "");
    }
    setTrackTitles(titles);
    setTrackArtists(artists);
    if (artist.trim().toLowerCase() === "various") setShowArtistCol(true);
  }

  useEffect(() => {
    setCoverError(false);
    setLoading(true);
    Promise.all([
      api.getDiscCandidates(disc.id),
      api.disc(disc.id),
    ]).then(([cands, discData]) => {
      setCandidates(cands);
      const sorted = (discData.tracks || [])
        .slice()
        .sort((a, b) => a.track_number - b.track_number);
      setPhysicalTracks(sorted);

      if (discData.album_title) {
        // Already identified — pre-fill from existing saved data
        setAlbumTitle(discData.album_title || "");
        const artist = discData.album_artist || "";
        setAlbumArtist(artist);
        const titles = new Map();
        const artists = new Map();
        for (const t of sorted) {
          titles.set(t.id, t.title || "");
          artists.set(t.id, t.artist || "");
        }
        setTrackTitles(titles);
        setTrackArtists(artists);
        if (artist.trim().toLowerCase() === "various") setShowArtistCol(true);
      } else if (cands.length > 0) {
        applyCandidate(cands[0], sorted);
        setCandidateIndex(0);
      } else {
        const titles = new Map();
        const artists = new Map();
        for (const t of sorted) { titles.set(t.id, ""); artists.set(t.id, ""); }
        setTrackTitles(titles);
        setTrackArtists(artists);
      }
      setLoading(false);
    }).catch((e) => {
      setLoadError(e.message);
      setLoading(false);
    });
  }, [disc.id]);

  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onSkip(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onSkip]);

  const hasCandidates = candidates.length > 0;
  const currentCandidate = hasCandidates ? candidates[candidateIndex] : null;

  // Thumbnail URL for the current candidate
  const coverThumbUrl = currentCandidate
    ? currentCandidate.source === "musicbrainz" && currentCandidate.mb_release_id
      ? `https://coverartarchive.org/release/${currentCandidate.mb_release_id}/front-250`
      : currentCandidate.image_url || null
    : null;

  const [coverError, setCoverError] = useState(false);

  function navToCandidate(idx) {
    setCandidateIndex(idx);
    setCoverError(false);
    applyCandidate(candidates[idx], physicalTracks);
  }

  function handleAlbumArtistChange(v) {
    setAlbumArtist(v);
    if (v.trim().toLowerCase() === "various") setShowArtistCol(true);
  }

  const setTitle = (id, v) =>
    setTrackTitles((prev) => new Map(prev).set(id, v));
  const setArtist = (id, v) =>
    setTrackArtists((prev) => new Map(prev).set(id, v));

  const isCompilation = albumArtist.trim().toLowerCase() === "various";

  const canConfirm =
    !confirming &&
    albumTitle.trim() !== "" &&
    physicalTracks.length > 0 &&
    physicalTracks.every((t) => (trackTitles.get(t.id) || "").trim() !== "") &&
    (!isCompilation ||
      physicalTracks.every((t) => (trackArtists.get(t.id) || "").trim() !== ""));

  async function handleConfirm() {
    if (!canConfirm) return;
    setConfirming(true);
    setConfirmError(null);
    try {
      const selectedCandidate = selectedCandidateId != null
        ? candidates.find((c) => c.id === selectedCandidateId)
        : null;
      const isDiscogs = selectedCandidate?.source === "discogs";
      let albumTitleToSave = albumTitle;
      let tempNameToSave = disc.temp_name;
      const multiDiscCount = isDiscogs
        ? selectedCandidate?.medium_count
        : disc.mb_medium_count;
      if (multiDiscCount > 1) {
        const discSuffix = (!isDiscogs && disc.mb_medium_title)
          ? disc.mb_medium_title
          : `Disc ${(isDiscogs ? selectedCandidate?.medium_position : disc.mb_medium_position) ?? "?"} of ${multiDiscCount}`;
        const suffixTag = `(${discSuffix})`;
        const suffixTagLower = suffixTag.toLowerCase();
        if (!albumTitle.toLowerCase().includes(suffixTagLower)) {
          albumTitleToSave = `${albumTitle} ${suffixTag}`;
        }
        if (!isDiscogs) {
          const baseTempName = disc.temp_name ?? albumTitle;
          if (!baseTempName.toLowerCase().includes(suffixTagLower)) {
            tempNameToSave = `${baseTempName} ${suffixTag}`;
          } else {
            tempNameToSave = baseTempName;
          }
        }
      }
      await api.identifyCd(disc.id, {
        album_title: albumTitleToSave,
        temp_name: tempNameToSave,
        album_artist: albumArtist || null,
        mb_release_id: selectedCandidate?.mb_release_id ?? null,
        tracks: physicalTracks.map((t) => ({
          id: t.id,
          title: trackTitles.get(t.id) || "",
          artist: trackArtists.get(t.id) || "",
        })),
        selected_candidate_id: selectedCandidateId,
      });
      onConfirm();
    } catch (e) {
      setConfirmError(e.message);
      setConfirming(false);
    }
  }

  return (
    <div className="identify-panel-overlay" onClick={onSkip}>
      <div
        className="identify-panel cd-identify-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="identify-panel-header">
          <div>
            <div className="identify-panel-title">Identify CD</div>
            <div className="identify-disc-name">{disc.temp_name || "Unnamed"}</div>
            {disc.disc_fingerprint && (
              <div className="identify-disc-fp">{disc.disc_fingerprint}</div>
            )}
          </div>
          <button className="mb-popover-close" onClick={onSkip}>×</button>
        </div>

        <div className="identify-panel-body">
          {loading && <div className="empty-state">Loading…</div>}

          {loadError && (
            <div className="identify-error" style={{ margin: "16px" }}>
              Failed to load: {loadError}
            </div>
          )}

          {!loading && !loadError && (
            <>
              {!hasCandidates && (
                <div className="cd-no-candidates-notice">
                  {disc.mb_lookup_status === "pending"
                    ? "MusicBrainz lookup in progress — enter track details manually while waiting"
                    : "No MusicBrainz matches found — enter track details manually"}
                </div>
              )}

              <div className="cd-album-section">
                {hasCandidates && (
                  <div className="cd-nav-row">
                    <button
                      className="cd-nav-btn"
                      onClick={() => navToCandidate(candidateIndex - 1)}
                      disabled={candidateIndex === 0}
                    >
                      ← Prev
                    </button>
                    <span className="cd-nav-counter">
                      {candidateIndex + 1} of {candidates.length}
                    </span>
                    {currentCandidate?.source === "discogs" ? (
                      <span className="candidate-source-badge candidate-source-badge--discogs">Discogs</span>
                    ) : currentCandidate?.source === "musicbrainz" ? (
                      <span className="candidate-source-badge candidate-source-badge--mb">MB</span>
                    ) : null}
                    <button
                      className="cd-nav-btn"
                      onClick={() => navToCandidate(candidateIndex + 1)}
                      disabled={candidateIndex === candidates.length - 1}
                    >
                      Next →
                    </button>
                  </div>
                )}

                {hasCandidates && (
                  <div className="cd-cover-row">
                    {coverThumbUrl && !coverError ? (
                      <img
                        key={coverThumbUrl}
                        src={coverThumbUrl}
                        className="cd-cover-thumb"
                        alt="Album cover"
                        onError={() => setCoverError(true)}
                      />
                    ) : (
                      <div className="cd-cover-placeholder">No cover</div>
                    )}
                  </div>
                )}

                {hasCandidates && currentCandidate?.medium_count > 1 && (
                  <div className="cd-disc-position">
                    {currentCandidate.medium_title
                      ? currentCandidate.medium_title
                      : `Disc ${currentCandidate.medium_position ?? "?"} of ${currentCandidate.medium_count}`}
                  </div>
                )}

                <div className="cd-album-row">
                  <span className="cd-album-label">Album</span>
                  <input
                    type="text"
                    className="cd-album-input"
                    value={albumTitle}
                    onChange={(e) => setAlbumTitle(e.target.value)}
                    placeholder="Album title"
                  />
                </div>

                <div className="cd-album-row">
                  <span className="cd-album-label">Artist</span>
                  <input
                    type="text"
                    className="cd-album-input"
                    value={albumArtist}
                    onChange={(e) => handleAlbumArtistChange(e.target.value)}
                    placeholder={'Album artist (use “Various” for compilations)'}
                  />
                </div>

                <div className="cd-album-toggles">
                  <label className="cd-compilation-toggle">
                    <input
                      type="checkbox"
                      checked={showArtistCol}
                      onChange={(e) => setShowArtistCol(e.target.checked)}
                    />
                    Show track artists
                  </label>

                  {disc.mb_medium_count > 1 && (
                    <label className="cd-compilation-toggle cd-multidisc-indicator">
                      <input type="checkbox" checked readOnly disabled />
                      Multi-disc · Disc {disc.mb_medium_position ?? "?"} of {disc.mb_medium_count}
                    </label>
                  )}
                </div>
              </div>

              <div className="cd-track-section">
                <div className="cd-track-wrap">
                  <table className="cd-track-table">
                    <colgroup>
                      <col className="cd-col-num" />
                      <col />
                      {showArtistCol && <col className="cd-col-artist" />}
                    </colgroup>
                    <thead>
                      <tr>
                        <th className="cd-col-num">#</th>
                        <th>Title</th>
                        {showArtistCol && <th>Artist</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {physicalTracks.map((track) => (
                        <tr key={track.id} className="cd-track-row">
                          <td className="cd-col-num cd-track-num">
                            {track.track_number}
                          </td>
                          <td>
                            <input
                              type="text"
                              className="cd-track-input"
                              value={trackTitles.get(track.id) || ""}
                              onChange={(e) => setTitle(track.id, e.target.value)}
                            />
                          </td>
                          {showArtistCol && (
                            <td>
                              <input
                                type="text"
                                className="cd-track-input"
                                value={trackArtists.get(track.id) || ""}
                                onChange={(e) => setArtist(track.id, e.target.value)}
                                placeholder={isCompilation ? "" : albumArtist}
                              />
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="identify-panel-footer">
          {confirmError && (
            <span className="identify-error cd-confirm-error">{confirmError}</span>
          )}
          <button onClick={onSkip} disabled={confirming}>Skip</button>
          <button onClick={handleConfirm} disabled={!canConfirm}>
            {confirming ? "Confirming…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
