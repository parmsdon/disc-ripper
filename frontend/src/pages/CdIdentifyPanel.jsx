import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export default function CdIdentifyPanel({ disc, onConfirm, onSkip }) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [physicalTracks, setPhysicalTracks] = useState([]);
  // Map<trackId, string> — presence of key = title locked
  const [lockedTitles, setLockedTitles] = useState(new Map());
  // Map<trackId, string> — presence of key = artist locked (compilation only)
  const [lockedArtists, setLockedArtists] = useState(new Map());
  const [albumTitleLock, setAlbumTitleLock] = useState(null);
  const [albumArtistLock, setAlbumArtistLock] = useState(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState(null);

  useEffect(() => {
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
      // No MB candidates: pre-lock all fields with empty strings for manual entry
      if (cands.length === 0) {
        setAlbumTitleLock("");
        setAlbumArtistLock("");
        const initTitles = new Map();
        for (const t of sorted) initTitles.set(t.id, "");
        setLockedTitles(initTitles);
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
  const isCompilation = albumArtistLock !== null &&
    albumArtistLock.trim().toLowerCase() === "various";

  function getMbTrack(trackNumber) {
    if (!currentCandidate?.tracks) return null;
    return currentCandidate.tracks.find(
      (t) => parseInt(t.number, 10) === trackNumber
    ) || null;
  }

  const lockedTitleCount = physicalTracks.filter(
    (t) => lockedTitles.has(t.id)
  ).length;
  const allTitlesLocked =
    physicalTracks.length > 0 && lockedTitleCount === physicalTracks.length;

  const canConfirm =
    !confirming &&
    albumTitleLock !== null &&
    albumTitleLock.trim() !== "" &&
    albumArtistLock !== null &&
    allTitlesLocked &&
    physicalTracks.every((t) => (lockedTitles.get(t.id) ?? "").trim() !== "") &&
    (!isCompilation ||
      physicalTracks.every((t) => {
        const a = lockedArtists.get(t.id);
        return a !== undefined && a.trim() !== "";
      }));

  function lockAllRemaining() {
    const newTitles = new Map(lockedTitles);
    const newArtists = new Map(lockedArtists);
    for (const track of physicalTracks) {
      if (!newTitles.has(track.id)) {
        const mbTrack = getMbTrack(track.track_number);
        newTitles.set(track.id, mbTrack?.title || "");
        if (isCompilation && !newArtists.has(track.id)) {
          newArtists.set(track.id, mbTrack?.artist || "");
        }
      }
    }
    setLockedTitles(newTitles);
    if (isCompilation) setLockedArtists(newArtists);
  }

  async function handleConfirm() {
    if (!canConfirm) return;
    setConfirming(true);
    setConfirmError(null);
    try {
      await api.identifyCd(disc.id, {
        album_title: albumTitleLock,
        album_artist: albumArtistLock,
        tracks: physicalTracks.map((t) => ({
          id: t.id,
          title: lockedTitles.get(t.id) || "",
          artist: lockedArtists.get(t.id) || "",
        })),
        selected_candidate_id: selectedCandidateId,
      });
      onConfirm();
    } catch (e) {
      setConfirmError(e.message);
      setConfirming(false);
    }
  }

  function handleLockTitle(trackId, mbTitle) {
    const next = new Map(lockedTitles);
    next.set(trackId, mbTitle || "");
    setLockedTitles(next);
  }

  function handleUnlockTitle(trackId) {
    const next = new Map(lockedTitles);
    next.delete(trackId);
    setLockedTitles(next);
  }

  function handleLockArtist(trackId, mbArtist) {
    const next = new Map(lockedArtists);
    next.set(trackId, mbArtist || "");
    setLockedArtists(next);
  }

  function handleUnlockArtist(trackId) {
    const next = new Map(lockedArtists);
    next.delete(trackId);
    setLockedArtists(next);
  }

  const lockAllDisabled = hasCandidates && allTitlesLocked &&
    (!isCompilation || physicalTracks.every((t) => lockedArtists.has(t.id)));

  return (
    <div className="identify-panel-overlay" onClick={onSkip}>
      <div
        className="identify-panel cd-identify-panel"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
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

        {/* Body */}
        <div className="identify-panel-body">

          {loading && (
            <div className="empty-state">Loading…</div>
          )}

          {loadError && (
            <div className="identify-error" style={{ margin: "16px" }}>
              Failed to load: {loadError}
            </div>
          )}

          {!loading && !loadError && (
            <>
              {/* No-candidates notice */}
              {!hasCandidates && (
                <div className="cd-no-candidates-notice">
                  {disc.mb_lookup_status === "pending"
                    ? "MusicBrainz lookup in progress — enter track details manually while waiting"
                    : "No MusicBrainz matches found — enter track details manually"}
                </div>
              )}

              {/* Album section */}
              <div className="cd-album-section">
                {hasCandidates && (
                  <div className="cd-nav-row">
                    <button
                      className="cd-nav-btn"
                      onClick={() => setCandidateIndex((i) => Math.max(0, i - 1))}
                      disabled={candidateIndex === 0}
                    >
                      ← Prev
                    </button>
                    <span className="cd-nav-counter">
                      {candidateIndex + 1} of {candidates.length}
                    </span>
                    <button
                      className="cd-nav-btn"
                      onClick={() =>
                        setCandidateIndex((i) =>
                          Math.min(candidates.length - 1, i + 1)
                        )
                      }
                      disabled={candidateIndex === candidates.length - 1}
                    >
                      Next →
                    </button>
                  </div>
                )}

                {/* Album title row */}
                <div className="cd-album-row">
                  <span className="cd-album-label">Album</span>
                  {hasCandidates && (
                    <span className="cd-mb-cell" title={currentCandidate?.title}>
                      {currentCandidate?.title || ""}
                    </span>
                  )}
                  <div className="cd-locked-cell">
                    {albumTitleLock !== null ? (
                      <input
                        type="text"
                        className="cd-album-input"
                        value={albumTitleLock}
                        onChange={(e) => setAlbumTitleLock(e.target.value)}
                      />
                    ) : (
                      <span className="cd-not-locked">not locked</span>
                    )}
                  </div>
                  {hasCandidates && (
                    <button
                      className={`cd-lock-btn${albumTitleLock !== null ? " cd-lock-btn-active" : ""}`}
                      onClick={() => {
                        if (albumTitleLock !== null) {
                          setAlbumTitleLock(null);
                        } else {
                          setAlbumTitleLock(currentCandidate?.title || "");
                          setSelectedCandidateId(currentCandidate?.id ?? null);
                        }
                      }}
                    >
                      {albumTitleLock !== null ? "Locked ✓" : "Lock"}
                    </button>
                  )}
                </div>

                {/* Album artist row */}
                <div className="cd-album-row">
                  <span className="cd-album-label">Artist</span>
                  {hasCandidates && (
                    <span className="cd-mb-cell" title={currentCandidate?.artist}>
                      {currentCandidate?.artist || ""}
                    </span>
                  )}
                  <div className="cd-locked-cell">
                    {albumArtistLock !== null ? (
                      <input
                        type="text"
                        className="cd-album-input"
                        value={albumArtistLock}
                        onChange={(e) => setAlbumArtistLock(e.target.value)}
                      />
                    ) : (
                      <span className="cd-not-locked">not locked</span>
                    )}
                  </div>
                  {hasCandidates && (
                    <button
                      className={`cd-lock-btn${albumArtistLock !== null ? " cd-lock-btn-active" : ""}`}
                      onClick={() => {
                        if (albumArtistLock !== null) {
                          setAlbumArtistLock(null);
                        } else {
                          setAlbumArtistLock(currentCandidate?.artist || "");
                          setSelectedCandidateId(currentCandidate?.id ?? null);
                        }
                      }}
                    >
                      {albumArtistLock !== null ? "Locked ✓" : "Lock"}
                    </button>
                  )}
                </div>
              </div>

              {/* Track section */}
              <div className="cd-track-section">
                {hasCandidates && (
                  <div className="cd-lock-all-row">
                    <button
                      className="cd-lock-all-btn"
                      onClick={lockAllRemaining}
                      disabled={lockAllDisabled}
                    >
                      {lockAllDisabled ? "All locked ✓" : "Lock All Remaining"}
                    </button>
                  </div>
                )}

                <div className="cd-track-wrap">
                  <table className="cd-track-table">
                    <colgroup>
                      <col className="cd-col-num" />
                      {hasCandidates && <col />}
                      <col />
                      {hasCandidates && <col className="cd-col-lock" />}
                      {isCompilation && hasCandidates && <col />}
                      {isCompilation && <col />}
                      {isCompilation && hasCandidates && <col className="cd-col-lock" />}
                    </colgroup>
                    <thead>
                      <tr>
                        <th className="cd-col-num">#</th>
                        {hasCandidates && <th>MB</th>}
                        <th>Title</th>
                        {hasCandidates && <th className="cd-col-lock"></th>}
                        {isCompilation && hasCandidates && <th>MB</th>}
                        {isCompilation && <th>Artist</th>}
                        {isCompilation && hasCandidates && (
                          <th className="cd-col-lock"></th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {physicalTracks.map((track) => {
                        const mbTrack = getMbTrack(track.track_number);
                        const titleLocked = lockedTitles.has(track.id);
                        const artistLocked = lockedArtists.has(track.id);

                        return (
                          <tr
                            key={track.id}
                            className={`cd-track-row${titleLocked ? " cd-track-locked" : ""}`}
                          >
                            <td className="cd-col-num cd-track-num">
                              {track.track_number}
                            </td>

                            {/* MB Title */}
                            {hasCandidates && (
                              <td className={`cd-mb-cell${titleLocked ? " cd-mb-dimmed" : ""}`}>
                                {mbTrack?.title || ""}
                              </td>
                            )}

                            {/* Locked Title */}
                            <td>
                              {titleLocked || !hasCandidates ? (
                                <input
                                  type="text"
                                  className="cd-track-input"
                                  value={lockedTitles.get(track.id) ?? ""}
                                  onChange={(e) => {
                                    const next = new Map(lockedTitles);
                                    next.set(track.id, e.target.value);
                                    setLockedTitles(next);
                                  }}
                                />
                              ) : (
                                <span className="cd-track-not-locked">—</span>
                              )}
                            </td>

                            {/* Lock Title button */}
                            {hasCandidates && (
                              <td className="cd-col-lock">
                                <button
                                  className={`cd-lock-btn${titleLocked ? " cd-lock-btn-active" : ""}`}
                                  onClick={() =>
                                    titleLocked
                                      ? handleUnlockTitle(track.id)
                                      : handleLockTitle(track.id, mbTrack?.title)
                                  }
                                >
                                  {titleLocked ? "✓" : "Lock"}
                                </button>
                              </td>
                            )}

                            {/* MB Artist (compilation + candidates) */}
                            {isCompilation && hasCandidates && (
                              <td className={`cd-mb-cell${artistLocked ? " cd-mb-dimmed" : ""}`}>
                                {mbTrack?.artist || ""}
                              </td>
                            )}

                            {/* Locked Artist (compilation) */}
                            {isCompilation && (
                              <td>
                                {artistLocked || !hasCandidates ? (
                                  <input
                                    type="text"
                                    className="cd-track-input"
                                    value={lockedArtists.get(track.id) ?? ""}
                                    onChange={(e) => {
                                      const next = new Map(lockedArtists);
                                      next.set(track.id, e.target.value);
                                      setLockedArtists(next);
                                    }}
                                  />
                                ) : (
                                  <span className="cd-track-not-locked">—</span>
                                )}
                              </td>
                            )}

                            {/* Lock Artist button (compilation + candidates) */}
                            {isCompilation && hasCandidates && (
                              <td className="cd-col-lock">
                                <button
                                  className={`cd-lock-btn${artistLocked ? " cd-lock-btn-active" : ""}`}
                                  onClick={() =>
                                    artistLocked
                                      ? handleUnlockArtist(track.id)
                                      : handleLockArtist(track.id, mbTrack?.artist)
                                  }
                                >
                                  {artistLocked ? "✓" : "Lock"}
                                </button>
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="identify-panel-footer">
          <span className="cd-lock-progress">
            {lockedTitleCount} / {physicalTracks.length} tracks locked
          </span>
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
