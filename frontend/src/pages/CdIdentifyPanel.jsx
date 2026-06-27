import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export default function CdIdentifyPanel({ disc, onConfirm, onSkip }) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [physicalTracks, setPhysicalTracks] = useState([]);

  // Saved (committed) lock state
  const [lockedTitles, setLockedTitles] = useState(new Map());
  const [lockedArtists, setLockedArtists] = useState(new Map());
  const [albumTitleLock, setAlbumTitleLock] = useState(null);
  const [albumArtistLock, setAlbumArtistLock] = useState(null);

  // Staging state: in-progress edits not yet saved (candidates mode only).
  // Presence of a key means the field is dirty. Value is the pending string.
  const [editingTitles, setEditingTitles] = useState(new Map());
  const [editingArtists, setEditingArtists] = useState(new Map());
  const [editingAlbumTitle, setEditingAlbumTitle] = useState(null);
  const [editingAlbumArtist, setEditingAlbumArtist] = useState(null);

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

  // Effective values: staging edit overrides saved lock (for canConfirm + submit)
  function effectiveTrackTitle(id) {
    return editingTitles.has(id) ? editingTitles.get(id) : (lockedTitles.get(id) ?? "");
  }
  function effectiveTrackArtist(id) {
    return editingArtists.has(id) ? editingArtists.get(id) : (lockedArtists.get(id) ?? "");
  }
  const effectiveAlbumTitle = editingAlbumTitle !== null ? editingAlbumTitle : albumTitleLock;
  const effectiveAlbumArtist = editingAlbumArtist !== null ? editingAlbumArtist : albumArtistLock;

  const isCompilation = effectiveAlbumArtist !== null &&
    effectiveAlbumArtist.trim().toLowerCase() === "various";

  function getMbTrack(trackNumber) {
    if (!currentCandidate?.tracks) return null;
    return currentCandidate.tracks.find(
      (t) => parseInt(t.number, 10) === trackNumber
    ) || null;
  }

  // Save staging edits into locked state
  function saveTrackTitle(id) {
    const val = editingTitles.get(id);
    if (val === undefined) return;
    setLockedTitles((prev) => new Map(prev).set(id, val));
    setEditingTitles((prev) => { const m = new Map(prev); m.delete(id); return m; });
  }
  function saveTrackArtist(id) {
    const val = editingArtists.get(id);
    if (val === undefined) return;
    setLockedArtists((prev) => new Map(prev).set(id, val));
    setEditingArtists((prev) => { const m = new Map(prev); m.delete(id); return m; });
  }
  function saveAlbumTitle() {
    if (editingAlbumTitle === null) return;
    setAlbumTitleLock(editingAlbumTitle);
    setEditingAlbumTitle(null);
  }
  function saveAlbumArtist() {
    if (editingAlbumArtist === null) return;
    setAlbumArtistLock(editingAlbumArtist);
    setEditingAlbumArtist(null);
  }

  // Lock / unlock (both clear staging for that field)
  function handleLockTitle(trackId, mbTitle) {
    setLockedTitles((prev) => new Map(prev).set(trackId, mbTitle || ""));
    setEditingTitles((prev) => { const m = new Map(prev); m.delete(trackId); return m; });
  }
  function handleUnlockTitle(trackId) {
    setLockedTitles((prev) => { const m = new Map(prev); m.delete(trackId); return m; });
    setEditingTitles((prev) => { const m = new Map(prev); m.delete(trackId); return m; });
  }
  function handleLockArtist(trackId, mbArtist) {
    setLockedArtists((prev) => new Map(prev).set(trackId, mbArtist || ""));
    setEditingArtists((prev) => { const m = new Map(prev); m.delete(trackId); return m; });
  }
  function handleUnlockArtist(trackId) {
    setLockedArtists((prev) => { const m = new Map(prev); m.delete(trackId); return m; });
    setEditingArtists((prev) => { const m = new Map(prev); m.delete(trackId); return m; });
  }

  function lockAllRemaining() {
    const newTitles = new Map(lockedTitles);
    const newArtists = new Map(lockedArtists);
    const newEditTitles = new Map(editingTitles);
    const newEditArtists = new Map(editingArtists);
    for (const track of physicalTracks) {
      if (!newTitles.has(track.id)) {
        const mbTrack = getMbTrack(track.track_number);
        newTitles.set(track.id, mbTrack?.title || "");
        newEditTitles.delete(track.id);
        if (isCompilation && !newArtists.has(track.id)) {
          newArtists.set(track.id, mbTrack?.artist || "");
          newEditArtists.delete(track.id);
        }
      }
    }
    setLockedTitles(newTitles);
    setEditingTitles(newEditTitles);
    if (isCompilation) {
      setLockedArtists(newArtists);
      setEditingArtists(newEditArtists);
    }
  }

  const lockedTitleCount = physicalTracks.filter((t) => lockedTitles.has(t.id)).length;
  const allTitlesLocked =
    physicalTracks.length > 0 && lockedTitleCount === physicalTracks.length;

  // canConfirm uses effective values so dirty-but-non-empty edits don't block it
  const canConfirm =
    !confirming &&
    (effectiveAlbumTitle ?? "").trim() !== "" &&
    effectiveAlbumArtist !== null &&
    allTitlesLocked &&
    physicalTracks.every((t) => effectiveTrackTitle(t.id).trim() !== "") &&
    (!isCompilation ||
      physicalTracks.every((t) => {
        return (
          (lockedArtists.has(t.id) || editingArtists.has(t.id)) &&
          effectiveTrackArtist(t.id).trim() !== ""
        );
      }));

  async function handleConfirm() {
    if (!canConfirm) return;
    setConfirming(true);
    setConfirmError(null);
    try {
      const selectedCandidate = selectedCandidateId != null
        ? candidates.find((c) => c.id === selectedCandidateId)
        : null;
      await api.identifyCd(disc.id, {
        album_title: effectiveAlbumTitle,
        album_artist: effectiveAlbumArtist,
        mb_release_id: selectedCandidate?.mb_release_id ?? null,
        tracks: physicalTracks.map((t) => ({
          id: t.id,
          title: effectiveTrackTitle(t.id),
          artist: effectiveTrackArtist(t.id),
        })),
        selected_candidate_id: selectedCandidateId,
      });
      onConfirm();
    } catch (e) {
      setConfirmError(e.message);
      setConfirming(false);
    }
  }

  const lockAllDisabled =
    hasCandidates &&
    allTitlesLocked &&
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
                        setCandidateIndex((i) => Math.min(candidates.length - 1, i + 1))
                      }
                      disabled={candidateIndex === candidates.length - 1}
                    >
                      Next →
                    </button>
                  </div>
                )}

                {hasCandidates && currentCandidate?.medium_count > 1 && (
                  <div className="cd-disc-position">
                    {currentCandidate.medium_title
                      ? currentCandidate.medium_title
                      : `Disc ${currentCandidate.medium_position ?? "?"} of ${currentCandidate.medium_count}`}
                  </div>
                )}

                {/* Album title row */}
                <AlbumRow
                  label="Album"
                  mbValue={currentCandidate?.title}
                  hasCandidates={hasCandidates}
                  lockedValue={albumTitleLock}
                  editingValue={editingAlbumTitle}
                  onType={(v) => setEditingAlbumTitle(v)}
                  onDirectChange={(v) => setAlbumTitleLock(v)}
                  onLock={() => {
                    setAlbumTitleLock(currentCandidate?.title || "");
                    setEditingAlbumTitle(null);
                    setSelectedCandidateId(currentCandidate?.id ?? null);
                  }}
                  onUnlock={() => { setAlbumTitleLock(null); setEditingAlbumTitle(null); }}
                  onSave={saveAlbumTitle}
                />

                {/* Album artist row */}
                <AlbumRow
                  label="Artist"
                  mbValue={currentCandidate?.artist}
                  hasCandidates={hasCandidates}
                  lockedValue={albumArtistLock}
                  editingValue={editingAlbumArtist}
                  onType={(v) => setEditingAlbumArtist(v)}
                  onDirectChange={(v) => setAlbumArtistLock(v)}
                  onLock={() => {
                    setAlbumArtistLock(currentCandidate?.artist || "");
                    setEditingAlbumArtist(null);
                    setSelectedCandidateId(currentCandidate?.id ?? null);
                  }}
                  onUnlock={() => { setAlbumArtistLock(null); setEditingAlbumArtist(null); }}
                  onSave={saveAlbumArtist}
                />

                {/* Compilation toggle */}
                <label className="cd-compilation-toggle">
                  <input
                    type="checkbox"
                    checked={isCompilation}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setAlbumArtistLock("Various");
                        setEditingAlbumArtist(null);
                      } else {
                        setAlbumArtistLock(hasCandidates ? null : "");
                        setEditingAlbumArtist(null);
                      }
                    }}
                  />
                  Compilation album
                </label>
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
                        const titleDirty = editingTitles.has(track.id);
                        const artistLocked = lockedArtists.has(track.id);
                        const artistDirty = editingArtists.has(track.id);

                        return (
                          <tr
                            key={track.id}
                            className={`cd-track-row${titleLocked ? " cd-track-locked" : ""}`}
                          >
                            <td className="cd-col-num cd-track-num">
                              {track.track_number}
                            </td>

                            {hasCandidates && (
                              <td className={`cd-mb-cell${titleLocked ? " cd-mb-dimmed" : ""}`}>
                                {mbTrack?.title || ""}
                              </td>
                            )}

                            <td>
                              {titleLocked || !hasCandidates ? (
                                <input
                                  type="text"
                                  className={`cd-track-input${titleDirty ? " cd-input-dirty" : ""}`}
                                  value={
                                    hasCandidates
                                      ? effectiveTrackTitle(track.id)
                                      : (lockedTitles.get(track.id) ?? "")
                                  }
                                  onChange={(e) => {
                                    if (hasCandidates) {
                                      setEditingTitles((prev) =>
                                        new Map(prev).set(track.id, e.target.value)
                                      );
                                    } else {
                                      setLockedTitles((prev) =>
                                        new Map(prev).set(track.id, e.target.value)
                                      );
                                    }
                                  }}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter" && titleDirty) {
                                      saveTrackTitle(track.id);
                                    }
                                  }}
                                />
                              ) : (
                                <span className="cd-track-not-locked">—</span>
                              )}
                            </td>

                            {hasCandidates && (
                              <td className="cd-col-lock">
                                <button
                                  className={`cd-lock-btn${
                                    titleLocked && !titleDirty ? " cd-lock-btn-active" : ""
                                  }${titleDirty ? " cd-lock-btn-save" : ""}`}
                                  onClick={() => {
                                    if (!titleLocked) {
                                      handleLockTitle(track.id, mbTrack?.title);
                                    } else if (titleDirty) {
                                      saveTrackTitle(track.id);
                                    } else {
                                      handleUnlockTitle(track.id);
                                    }
                                  }}
                                >
                                  {!titleLocked ? "Lock" : titleDirty ? "Save" : "✓"}
                                </button>
                              </td>
                            )}

                            {isCompilation && hasCandidates && (
                              <td className={`cd-mb-cell${artistLocked ? " cd-mb-dimmed" : ""}`}>
                                {mbTrack?.artist || ""}
                              </td>
                            )}

                            {isCompilation && (
                              <td>
                                {artistLocked || !hasCandidates ? (
                                  <input
                                    type="text"
                                    className={`cd-track-input${artistDirty ? " cd-input-dirty" : ""}`}
                                    value={
                                      hasCandidates
                                        ? effectiveTrackArtist(track.id)
                                        : (lockedArtists.get(track.id) ?? "")
                                    }
                                    onChange={(e) => {
                                      if (hasCandidates) {
                                        setEditingArtists((prev) =>
                                          new Map(prev).set(track.id, e.target.value)
                                        );
                                      } else {
                                        setLockedArtists((prev) =>
                                          new Map(prev).set(track.id, e.target.value)
                                        );
                                      }
                                    }}
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter" && artistDirty) {
                                        saveTrackArtist(track.id);
                                      }
                                    }}
                                  />
                                ) : (
                                  <span className="cd-track-not-locked">—</span>
                                )}
                              </td>
                            )}

                            {isCompilation && hasCandidates && (
                              <td className="cd-col-lock">
                                <button
                                  className={`cd-lock-btn${
                                    artistLocked && !artistDirty ? " cd-lock-btn-active" : ""
                                  }${artistDirty ? " cd-lock-btn-save" : ""}`}
                                  onClick={() => {
                                    if (!artistLocked) {
                                      handleLockArtist(track.id, mbTrack?.artist);
                                    } else if (artistDirty) {
                                      saveTrackArtist(track.id);
                                    } else {
                                      handleUnlockArtist(track.id);
                                    }
                                  }}
                                >
                                  {!artistLocked ? "Lock" : artistDirty ? "Save" : "✓"}
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

function AlbumRow({
  label, mbValue, hasCandidates,
  lockedValue, editingValue,
  onType, onDirectChange, onLock, onUnlock, onSave,
}) {
  const isLocked = lockedValue !== null;
  const isDirty = editingValue !== null;
  const displayValue = isDirty ? editingValue : (lockedValue ?? "");

  return (
    <div className="cd-album-row">
      <span className="cd-album-label">{label}</span>
      {hasCandidates && (
        <span className="cd-mb-cell" title={mbValue}>{mbValue || ""}</span>
      )}
      <div className="cd-locked-cell">
        {isLocked ? (
          <input
            type="text"
            className={`cd-album-input${isDirty ? " cd-input-dirty" : ""}`}
            value={displayValue}
            onChange={(e) =>
              hasCandidates ? onType(e.target.value) : onDirectChange(e.target.value)
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" && isDirty) onSave();
            }}
          />
        ) : (
          <span className="cd-not-locked">not locked</span>
        )}
      </div>
      {hasCandidates && (
        <button
          className={`cd-lock-btn${
            isLocked && !isDirty ? " cd-lock-btn-active" : ""
          }${isDirty ? " cd-lock-btn-save" : ""}`}
          onClick={() => {
            if (!isLocked) onLock();
            else if (isDirty) onSave();
            else onUnlock();
          }}
        >
          {!isLocked ? "Lock" : isDirty ? "Save" : "Locked ✓"}
        </button>
      )}
    </div>
  );
}
