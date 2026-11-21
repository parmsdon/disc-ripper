import React, { useEffect, useRef, useState } from "react";
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
  const [isCompilation, setIsCompilation] = useState(false);
  const [preCompilationArtist, setPreCompilationArtist] = useState("");

  // Lock state — locked fields are not overwritten when navigating candidates
  const [albumTitleLocked, setAlbumTitleLocked] = useState(false);
  const [albumArtistLocked, setAlbumArtistLocked] = useState(false);
  const [lockedTitleIds, setLockedTitleIds] = useState(new Set());
  const [lockedArtistIds, setLockedArtistIds] = useState(new Set());

  const [selectedCandidateId, setSelectedCandidateId] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState(null);
  // Per-candidate cover load errors (by candidate id)
  const [coverErrors, setCoverErrors] = useState(new Set());
  // Per-candidate cover load successes
  const [coverLoadedIds, setCoverLoadedIds] = useState(new Set());
  // Keep Image objects alive until loaded so GC doesn't fire onerror prematurely
  const preloadRefs = useRef([]);
  // Whether candidates are still loading (after disc data is ready)
  const [candidatesLoading, setCandidatesLoading] = useState(true);
  // Cover lock — when true, the cover box stays on coverLockedCandidateId
  const [coverLocked, setCoverLocked] = useState(false);
  const [coverLockedCandidateId, setCoverLockedCandidateId] = useState(null);

  function getCoverUrl(cand) {
    if (!cand) return null;
    return cand.source === "musicbrainz" && cand.mb_release_id
      ? `https://coverartarchive.org/release/${cand.mb_release_id}/front-250`
      : cand.image_url || null;
  }

  // Preload all candidate cover thumbnails when the candidate list loads.
  // Image objects are kept in a ref so GC can't fire onerror before the 1-2s CAA
  // redirect chain completes. Only onload marks a cover ready; errors are handled
  // by the display <img> onError so a preload timing issue never hides a cover.
  useEffect(() => {
    preloadRefs.current = [];
    for (const cand of candidates) {
      const url = getCoverUrl(cand);
      if (!url) continue;
      const img = new Image();
      img.onload = () => setCoverLoadedIds((prev) => new Set(prev).add(cand.id));
      img.src = url;
      preloadRefs.current.push(img);
    }
  }, [candidates]);

  // applyCandidate: fills only unlocked fields from the given candidate
  function applyCandidate(cand, tracks) {
    if (!albumTitleLocked) setAlbumTitle(cand.title || "");
    if (!albumArtistLocked && !isCompilation) {
      const artist = cand.artist || "";
      const various = ["various", "various artists"].includes(artist.trim().toLowerCase());
      setAlbumArtist(various ? "Various" : artist);
      setIsCompilation(various);
      setPreCompilationArtist("");
      if (various) setAlbumArtistLocked(true);
    }
    setSelectedCandidateId(cand.id);
    const newTitles = new Map(trackTitles);
    const newArtists = new Map(trackArtists);
    for (const track of tracks) {
      const mbTrack = cand.tracks?.find(
        (t) => parseInt(t.number, 10) === track.track_number
      );
      if (!lockedTitleIds.has(track.id)) newTitles.set(track.id, mbTrack?.title || "");
      if (!lockedArtistIds.has(track.id)) newArtists.set(track.id, mbTrack?.artist || "");
    }
    setTrackTitles(newTitles);
    setTrackArtists(newArtists);
  }

  useEffect(() => {
    setLoading(true);
    setCandidatesLoading(true);

    // Phase 1: load disc data so the form renders immediately (local DB, fast).
    // Phase 2: load candidates after (may trigger external lookup — kept separate
    //           so a slow MusicBrainz/Discogs call never delays the form).
    let localTracks = [];
    let discHasAlbum = false;
    let discLoadDone = false;

    api.disc(disc.id)
      .then((discData) => {
        const sorted = (discData.tracks || [])
          .slice()
          .sort((a, b) => a.track_number - b.track_number);
        localTracks = sorted;
        setPhysicalTracks(sorted);

        if (discData.album_title) {
          // Already identified — pre-fill from saved data
          discHasAlbum = true;
          setAlbumTitle(discData.album_title || "");
          const artist = discData.album_artist || "";
          const various = ["various", "various artists"].includes(artist.trim().toLowerCase());
          setAlbumArtist(various ? "Various" : artist);
          setIsCompilation(various);
          setPreCompilationArtist("");
          if (various) setAlbumArtistLocked(true);
          const titles = new Map();
          const artists = new Map();
          for (const t of sorted) {
            titles.set(t.id, t.title || "");
            artists.set(t.id, t.artist || "");
          }
          setTrackTitles(titles);
          setTrackArtists(artists);
        } else {
          // Empty maps — will be filled when candidates arrive
          const titles = new Map();
          const artists = new Map();
          for (const t of sorted) { titles.set(t.id, ""); artists.set(t.id, ""); }
          setTrackTitles(titles);
          setTrackArtists(artists);
        }
        discLoadDone = true;
        setLoading(false);
        return api.getDiscCandidates(disc.id);
      })
      .then((cands) => {
        setCandidates(cands);
        if (!discHasAlbum && cands.length > 0) {
          // First candidate — populate all fields (nothing is locked yet)
          const first = cands[0];
          setAlbumTitle(first.title || "");
          const artist = first.artist || "";
          const various = ["various", "various artists"].includes(artist.trim().toLowerCase());
          setAlbumArtist(various ? "Various" : artist);
          setIsCompilation(various);
          setPreCompilationArtist("");
          if (various) setAlbumArtistLocked(true);
          setSelectedCandidateId(first.id);
          setCandidateIndex(0);
          const titles = new Map();
          const artists = new Map();
          for (const t of localTracks) {
            const mbTrack = first.tracks?.find(
              (mt) => parseInt(mt.number, 10) === t.track_number
            );
            titles.set(t.id, mbTrack?.title || "");
            artists.set(t.id, mbTrack?.artist || "");
          }
          setTrackTitles(titles);
          setTrackArtists(artists);
        }
        setCandidatesLoading(false);
      })
      .catch((e) => {
        if (!discLoadDone) {
          setLoadError(e.message);
          setLoading(false);
        }
        // If only candidates failed, the form still works in manual-entry mode
        setCandidatesLoading(false);
      });
  }, [disc.id]);

  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onSkip(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onSkip]);

  const hasCandidates = candidates.length > 0;
  const currentCandidate = hasCandidates ? candidates[candidateIndex] : null;

  function navToCandidate(idx) {
    setCandidateIndex(idx);
    applyCandidate(candidates[idx], physicalTracks);
  }

  function handleCompilationToggle(checked) {
    if (checked) {
      setPreCompilationArtist(albumArtist);
      setAlbumArtist("Various");
      setAlbumArtistLocked(true);
    } else {
      setAlbumArtist(preCompilationArtist);
      setPreCompilationArtist("");
      setAlbumArtistLocked(false);
    }
    setIsCompilation(checked);
  }

  // Typing auto-locks the field
  const setTitle = (id, v) => {
    setTrackTitles((prev) => new Map(prev).set(id, v));
    setLockedTitleIds((prev) => new Set(prev).add(id));
  };
  const setArtist = (id, v) => {
    setTrackArtists((prev) => new Map(prev).set(id, v));
    setLockedArtistIds((prev) => new Set(prev).add(id));
  };
  function handleAlbumTitleChange(v) {
    setAlbumTitle(v);
    setAlbumTitleLocked(true);
  }
  function handleAlbumArtistChange(v) {
    setAlbumArtist(v);
    setAlbumArtistLocked(true);
  }

  function handleCoverLock() {
    if (coverLocked) {
      setCoverLocked(false);
      setCoverLockedCandidateId(null);
    } else {
      setCoverLocked(true);
      setCoverLockedCandidateId(currentCandidate?.id ?? null);
    }
  }

  function lockAllRemaining() {
    setLockedTitleIds((prev) => new Set([...prev, ...physicalTracks.map((t) => t.id)]));
    if (isCompilation) {
      setLockedArtistIds((prev) => new Set([...prev, ...physicalTracks.map((t) => t.id)]));
    }
  }

  const lockAllDisabled =
    physicalTracks.length > 0 &&
    physicalTracks.every((t) => lockedTitleIds.has(t.id)) &&
    (!isCompilation || physicalTracks.every((t) => lockedArtistIds.has(t.id)));

  const lockedTitleCount = physicalTracks.filter((t) => lockedTitleIds.has(t.id)).length;

  // With candidates: all fields must be locked (and non-empty) to confirm.
  // Without candidates: no lock buttons exist, so only non-empty is required.
  const canConfirm =
    !confirming &&
    albumTitle.trim() !== "" &&
    (!hasCandidates || albumTitleLocked) &&
    albumArtist.trim() !== "" &&
    (!hasCandidates || albumArtistLocked || isCompilation) &&
    physicalTracks.length > 0 &&
    physicalTracks.every((t) =>
      (trackTitles.get(t.id) || "").trim() !== "" &&
      (!hasCandidates || lockedTitleIds.has(t.id))
    ) &&
    (!isCompilation ||
      physicalTracks.every((t) =>
        (trackArtists.get(t.id) || "").trim() !== "" &&
        (!hasCandidates || lockedArtistIds.has(t.id))
      ));

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
        cover_candidate_id: coverLocked ? coverLockedCandidateId : null,
      });
      onConfirm();
    } catch (e) {
      setConfirmError(e.message);
      setConfirming(false);
    }
  }

  const lockedCoverCand = coverLocked
    ? (candidates.find((c) => c.id === coverLockedCandidateId) ?? null)
    : null;
  const displayCoverCand = lockedCoverCand ?? currentCandidate;
  const coverUrl = getCoverUrl(displayCoverCand);
  const coverFailed = coverErrors.has(displayCoverCand?.id);
  const coverLoaded = coverLoadedIds.has(displayCoverCand?.id);
  const coverIsLoading = !!coverUrl && !coverFailed && !coverLoaded;

  // Mini thumb shows the current candidate's cover when it differs from the locked cover
  const currentCandCoverUrl = getCoverUrl(currentCandidate);
  const showCurrentThumb = coverLocked &&
    !!currentCandCoverUrl &&
    currentCandCoverUrl !== coverUrl;
  const currentThumbLoaded = coverLoadedIds.has(currentCandidate?.id);
  const currentThumbFailed = coverErrors.has(currentCandidate?.id);
  const currentThumbIsLoading = showCurrentThumb && !currentThumbFailed && !currentThumbLoaded;

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
              {!hasCandidates && !candidatesLoading && (
                <div className="cd-no-candidates-notice">
                  {disc.mb_lookup_status === "pending"
                    ? "MusicBrainz lookup in progress — enter track details manually while waiting"
                    : "No MusicBrainz matches found — enter track details manually"}
                </div>
              )}

              <div className="cd-album-section">
                {(hasCandidates || candidatesLoading) && (
                  <div className="cd-nav-row">
                    {candidatesLoading ? (
                      <span className="cd-nav-counter">Loading matches…</span>
                    ) : (
                      <>
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
                      </>
                    )}
                  </div>
                )}

                {hasCandidates && (
                  <div className="cd-cover-row">
                    <div className="cd-cover-box">
                      {coverUrl && !coverFailed && (
                        <img
                          key={coverUrl}
                          src={coverUrl}
                          className="cd-cover-thumb"
                          style={coverLoaded ? {} : { display: "none" }}
                          alt="Album cover"
                          onLoad={() =>
                            setCoverLoadedIds((prev) => new Set(prev).add(displayCoverCand?.id))
                          }
                          onError={() =>
                            setCoverErrors((prev) => new Set(prev).add(displayCoverCand?.id))
                          }
                        />
                      )}
                      {coverIsLoading && (
                        <div className="cd-cover-placeholder">Loading…</div>
                      )}
                      {(!coverUrl || coverFailed) && (
                        <div className="cd-cover-placeholder">No cover</div>
                      )}
                    </div>
                    <div className="cd-cover-controls">
                      <button
                        className={`cd-lock-btn${coverLocked ? " cd-lock-btn-active" : ""}`}
                        onClick={handleCoverLock}
                      >
                        {coverLocked ? "Locked ✓" : "Lock"}
                      </button>
                      {showCurrentThumb && (
                        <div className="cd-cover-mini-wrap">
                          <div className="cd-cover-mini-box">
                            {!currentThumbFailed && (
                              <img
                                key={currentCandCoverUrl}
                                src={currentCandCoverUrl}
                                className="cd-cover-mini-thumb"
                                style={currentThumbLoaded ? {} : { display: "none" }}
                                alt="Current candidate cover"
                                onLoad={() =>
                                  setCoverLoadedIds((prev) => new Set(prev).add(currentCandidate?.id))
                                }
                                onError={() =>
                                  setCoverErrors((prev) => new Set(prev).add(currentCandidate?.id))
                                }
                              />
                            )}
                            {currentThumbIsLoading && (
                              <div className="cd-cover-mini-placeholder">…</div>
                            )}
                            {(!currentCandCoverUrl || currentThumbFailed) && (
                              <div className="cd-cover-mini-placeholder">None</div>
                            )}
                          </div>
                          <div className="cd-cover-mini-label">Current</div>
                        </div>
                      )}
                    </div>
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
                  <div className="cd-field-col">
                    <input
                      type="text"
                      className="cd-album-input"
                      value={albumTitle}
                      onChange={(e) => handleAlbumTitleChange(e.target.value)}
                      placeholder="Album title"
                    />
                    {hasCandidates && albumTitleLocked &&
                      currentCandidate?.title && currentCandidate.title !== albumTitle && (
                      <div className="cd-cand-hint">{currentCandidate.title}</div>
                    )}
                  </div>
                  {hasCandidates && (
                    <button
                      className={`cd-lock-btn${albumTitleLocked ? " cd-lock-btn-active" : ""}`}
                      onClick={() => setAlbumTitleLocked((v) => !v)}
                    >
                      {albumTitleLocked ? "Locked ✓" : "Lock"}
                    </button>
                  )}
                </div>

                <div className="cd-album-row">
                  <span className="cd-album-label">Artist</span>
                  <div className="cd-field-col">
                    <input
                      type="text"
                      className="cd-album-input"
                      value={albumArtist}
                      onChange={(e) => handleAlbumArtistChange(e.target.value)}
                      placeholder="Album artist"
                    />
                    {hasCandidates && albumArtistLocked && !isCompilation &&
                      currentCandidate?.artist && currentCandidate.artist !== albumArtist && (
                      <div className="cd-cand-hint">{currentCandidate.artist}</div>
                    )}
                  </div>
                  {hasCandidates && !isCompilation && (
                    <button
                      className={`cd-lock-btn${albumArtistLocked ? " cd-lock-btn-active" : ""}`}
                      onClick={() => setAlbumArtistLocked((v) => !v)}
                    >
                      {albumArtistLocked ? "Locked ✓" : "Lock"}
                    </button>
                  )}
                </div>

                <div className="cd-album-toggles">
                  <label className="cd-compilation-toggle">
                    <input
                      type="checkbox"
                      checked={isCompilation}
                      onChange={(e) => handleCompilationToggle(e.target.checked)}
                    />
                    Compilation
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
                      <col />
                      {hasCandidates && <col className="cd-col-lock" />}
                      {isCompilation && <col className="cd-col-artist" />}
                      {isCompilation && hasCandidates && <col className="cd-col-lock" />}
                    </colgroup>
                    <thead>
                      <tr>
                        <th className="cd-col-num">#</th>
                        <th>Title</th>
                        {hasCandidates && <th className="cd-col-lock"></th>}
                        {isCompilation && <th>Artist</th>}
                        {isCompilation && hasCandidates && <th className="cd-col-lock"></th>}
                      </tr>
                    </thead>
                    <tbody>
                      {physicalTracks.map((track) => {
                        const titleLocked = lockedTitleIds.has(track.id);
                        const artistLocked = lockedArtistIds.has(track.id);
                        const mbTrack = currentCandidate?.tracks?.find(
                          (t) => parseInt(t.number, 10) === track.track_number
                        );
                        const titleHint = hasCandidates && titleLocked &&
                          mbTrack?.title && mbTrack.title !== (trackTitles.get(track.id) || "")
                          ? mbTrack.title : null;
                        const artistHint = hasCandidates && artistLocked &&
                          mbTrack?.artist && mbTrack.artist !== (trackArtists.get(track.id) || "")
                          ? mbTrack.artist : null;
                        return (
                          <tr
                            key={track.id}
                            className={`cd-track-row${titleLocked ? " cd-track-locked" : ""}`}
                          >
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
                              {titleHint && <div className="cd-cand-hint">{titleHint}</div>}
                            </td>
                            {hasCandidates && (
                              <td className="cd-col-lock">
                                <button
                                  className={`cd-lock-btn${titleLocked ? " cd-lock-btn-active" : ""}`}
                                  onClick={() =>
                                    setLockedTitleIds((prev) => {
                                      const s = new Set(prev);
                                      if (s.has(track.id)) s.delete(track.id);
                                      else s.add(track.id);
                                      return s;
                                    })
                                  }
                                >
                                  {titleLocked ? "✓" : "Lock"}
                                </button>
                              </td>
                            )}
                            {isCompilation && (
                              <td>
                                <input
                                  type="text"
                                  className="cd-track-input"
                                  value={trackArtists.get(track.id) || ""}
                                  onChange={(e) => setArtist(track.id, e.target.value)}
                                />
                                {artistHint && <div className="cd-cand-hint">{artistHint}</div>}
                              </td>
                            )}
                            {isCompilation && hasCandidates && (
                              <td className="cd-col-lock">
                                <button
                                  className={`cd-lock-btn${artistLocked ? " cd-lock-btn-active" : ""}`}
                                  onClick={() =>
                                    setLockedArtistIds((prev) => {
                                      const s = new Set(prev);
                                      if (s.has(track.id)) s.delete(track.id);
                                      else s.add(track.id);
                                      return s;
                                    })
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

        <div className="identify-panel-footer">
          {confirmError ? (
            <span className="identify-error cd-confirm-error">{confirmError}</span>
          ) : hasCandidates ? (
            <span className="cd-lock-progress">
              {lockedTitleCount} / {physicalTracks.length} locked
            </span>
          ) : null}
          <button onClick={onSkip} disabled={confirming}>Skip</button>
          <button onClick={handleConfirm} disabled={!canConfirm}>
            {confirming ? "Confirming…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
