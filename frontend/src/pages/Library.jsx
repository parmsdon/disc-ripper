import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

function formatDatetime(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function PrereqDot({ tone }) {
  return <span className={`prereq-dot prereq-dot--${tone}`} />;
}

function PrereqRow({ label, count, warnNotError = false }) {
  const tone = count === 0 ? "ok" : warnNotError ? "warn" : "error";
  return (
    <div className="prereq-row">
      <PrereqDot tone={tone} />
      <span className="prereq-label">{label}</span>
      <span className="prereq-count">{count ?? "—"}</span>
    </div>
  );
}

function StatCard({ value, label }) {
  const tone = label === "Errors" && value > 0 ? " stat-error" : "";
  return (
    <div className={`stat${tone}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export default function Library() {
  const [status, setStatus] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [genResult, setGenResult] = useState(null);
  const [genError, setGenError] = useState(null);

  const fetchStatus = useCallback(() => {
    setLoadError(null);
    api.getLibraryStatus()
      .then(setStatus)
      .catch((e) => setLoadError(e.message));
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  async function handleGenerate() {
    setGenerating(true);
    setGenResult(null);
    setGenError(null);
    try {
      const result = await api.generateLibrary();
      setGenResult(result);
      fetchStatus();
    } catch (e) {
      setGenError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  const prereqs = status?.prerequisites;
  const blockingCount = prereqs
    ? Object.values(prereqs).filter((v) => v > 0).length
    : null;
  const ready = status?.ready ?? false;

  return (
    <div>
      <div className="panel">
        <div className="panel-heading-row">
          <h2>Library</h2>
          <button onClick={fetchStatus}>Refresh Status</button>
        </div>

        {loadError && (
          <div className="empty-state" style={{ color: "var(--error)" }}>Error: {loadError}</div>
        )}

        <h3 className="library-section-heading">Prerequisites</h3>
        <div className="library-prerequisites">
          <PrereqRow
            label="DVDs unmatched to My Movies"
            count={prereqs?.dvds_unmatched}
          />
          <PrereqRow
            label="CDs unidentified"
            count={prereqs?.cds_unidentified}
          />
          <PrereqRow
            label="CD tracks untitled"
            count={prereqs?.cd_tracks_untitled}
          />
          <PrereqRow
            label="DVD encodes pending"
            count={prereqs?.dvd_encodes_pending}
          />
          <PrereqRow
            label="CD encodes pending"
            count={prereqs?.cd_encodes_pending}
          />
          <PrereqRow
            label="DVDs still ripping"
            count={prereqs?.dvds_not_ripped}
          />
          <PrereqRow
            label="CDs still ripping"
            count={prereqs?.cds_not_ripped}
          />
        </div>

        <div className="library-generate-zone">
          <button
            className={`library-generate-btn${ready ? " ready" : ""}`}
            onClick={handleGenerate}
            disabled={!ready || generating}
          >
            {generating ? "Generating…" : "Generate Library"}
          </button>
          {!ready && blockingCount !== null && (
            <span className="library-blocked-hint">
              {blockingCount} {blockingCount === 1 ? "item" : "items"} blocking generation
            </span>
          )}
          {genResult && (
            <span className="library-gen-ok">
              Done — {genResult.dvd_iso + genResult.dvd_plex + genResult.dvd_iphone + genResult.cd_flac + genResult.cd_mp3} symlinks in {genResult.duration_seconds}s
              {genResult.errors > 0 && ` (${genResult.errors} errors)`}
            </span>
          )}
          {genError && (
            <span className="library-gen-error">Error: {genError}</span>
          )}
        </div>
      </div>

      {status?.last_generated && (
        <div className="panel">
          <h2>Last Generated</h2>
          <p className="library-last-generated">
            {formatDatetime(status.last_generated)}
          </p>
          {status.last_stats && (
            <div className="library-stats">
              <StatCard value={status.last_stats.dvd_iso}   label="DVD ISO symlinks" />
              <StatCard value={status.last_stats.dvd_plex}  label="DVD Plex symlinks" />
              <StatCard value={status.last_stats.dvd_iphone} label="DVD iPhone symlinks" />
              <StatCard value={status.last_stats.cd_flac}   label="CD FLAC symlinks" />
              <StatCard value={status.last_stats.cd_mp3}    label="CD MP3 symlinks" />
              <StatCard value={status.last_stats.errors}    label="Errors" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
