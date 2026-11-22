import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

function formatDatetime(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function elapsed(isoStart) {
  if (!isoStart) return "";
  const secs = Math.round((Date.now() - new Date(isoStart).getTime()) / 1000);
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60), s = secs % 60;
  if (m < 60) return `${m}m ${s}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
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

function ProgressBar({ done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="lib-progress-track">
      <div className="lib-progress-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function Library() {
  const [status, setStatus] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [startError, setStartError] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [now, setNow] = useState(Date.now());

  const fetchStatus = useCallback(() => {
    api.getLibraryStatus()
      .then((s) => { setStatus(s); setLoadError(null); })
      .catch((e) => setLoadError(e.message));
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  // Poll while running
  useEffect(() => {
    if (status?.gen_status !== "running") return;
    const timer = setInterval(fetchStatus, 2000);
    return () => clearInterval(timer);
  }, [status?.gen_status, fetchStatus]);

  // Update elapsed timer once/second when running
  useEffect(() => {
    if (status?.gen_status !== "running") return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [status?.gen_status]);

  async function handleGenerate() {
    if (generating) return;
    setGenerating(true);
    setStartError(null);
    try {
      await api.generateLibrary();
      fetchStatus();
    } catch (e) {
      if (e.message.includes("already_running")) {
        // Another request beat us — just show its progress
        fetchStatus();
      } else {
        setStartError(e.message);
      }
    } finally {
      setGenerating(false);
    }
  }

  const prereqs = status?.prerequisites;
  const blockingCount = prereqs
    ? Object.values(prereqs).filter((v) => v > 0).length
    : null;
  const ready = status?.ready ?? false;
  const genStatus = status?.gen_status ?? "idle";
  const isRunning = genStatus === "running";
  const isBusy = isRunning || generating;
  const prog = status?.gen_progress;

  return (
    <div>
      <div className="panel">
        <div className="panel-heading-row">
          <h2>Library</h2>
          <button onClick={fetchStatus}>Refresh</button>
        </div>

        {loadError && (
          <div className="empty-state" style={{ color: "var(--error)" }}>Error: {loadError}</div>
        )}

        <h3 className="library-section-heading">Prerequisites</h3>
        <div className="library-prerequisites">
          <PrereqRow label="DVDs unmatched to My Movies" count={prereqs?.dvds_unmatched} />
          <PrereqRow label="CDs unidentified" count={prereqs?.cds_unidentified} />
          <PrereqRow label="CD tracks untitled" count={prereqs?.cd_tracks_untitled} />
          <PrereqRow label="DVD encodes pending" count={prereqs?.dvd_encodes_pending} />
          <PrereqRow label="CD encodes pending" count={prereqs?.cd_encodes_pending} />
          <PrereqRow label="DVDs still ripping" count={prereqs?.dvds_not_ripped} />
          <PrereqRow label="CDs still ripping" count={prereqs?.cds_not_ripped} />
          <PrereqRow label="Cover art downloads in progress" count={prereqs?.cd_covers_fetching} warnNotError />
        </div>

        <div className="library-generate-zone">
          <button
            className={`library-generate-btn${ready ? " ready" : ""}`}
            onClick={handleGenerate}
            disabled={!ready || isBusy}
          >
            {isBusy ? "Running…" : "Generate Library"}
          </button>
          {!ready && blockingCount !== null && !isBusy && (
            <span className="library-blocked-hint">
              {blockingCount} {blockingCount === 1 ? "item" : "items"} blocking generation
            </span>
          )}
          {startError && (
            <span className="library-gen-error">Error: {startError}</span>
          )}
        </div>
      </div>

      {/* Running / progress view */}
      {isRunning && (
        <div className="panel">
          <h2>Generating…</h2>
          <div className="lib-progress-section">
            {prog && prog.discs_total > 0 && (
              <>
                <ProgressBar done={prog.discs_done} total={prog.discs_total} />
                <div className="lib-progress-counts">
                  {prog.discs_done} of {prog.discs_total} discs
                </div>
              </>
            )}
            {prog?.step && (
              <div className="lib-progress-step">{prog.step}</div>
            )}
            {status?.gen_started_at && (
              <div className="lib-progress-elapsed">
                Elapsed: {elapsed(status.gen_started_at)}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Failed / interrupted view */}
      {(genStatus === "failed" || genStatus === "interrupted") && (
        <div className="panel">
          <h2 style={{ color: "var(--error)" }}>
            {genStatus === "interrupted" ? "Generation Interrupted" : "Generation Failed"}
          </h2>
          <p className="lib-error-msg">
            {genStatus === "interrupted"
              ? "The generation job stopped unexpectedly (API restart or timeout). The library may be incomplete."
              : (status?.gen_error || "An unknown error occurred.")}
          </p>
          <p className="lib-error-hint">Re-running will wipe and rebuild the library from scratch.</p>
        </div>
      )}

      {/* Last generated report */}
      {status?.last_generated && (
        <div className="panel">
          <h2>Last Generated</h2>
          <p className="library-last-generated">{formatDatetime(status.last_generated)}</p>
          {status.last_stats && (
            <div className="library-stats">
              <StatCard value={status.last_stats.dvd_iso}    label="DVD ISO symlinks" />
              <StatCard value={status.last_stats.dvd_plex}   label="DVD Plex symlinks" />
              <StatCard value={status.last_stats.dvd_iphone} label="DVD iPhone symlinks" />
              <StatCard value={status.last_stats.cd_flac}    label="CD FLAC symlinks" />
              <StatCard value={status.last_stats.cd_mp3}     label="CD MP3 symlinks" />
              <StatCard value={status.last_stats.cd_files_tagged ?? 0}    label="CD files tagged" />
              <StatCard value={status.last_stats.cd_covers_embedded ?? 0} label="CD covers embedded" />
              <StatCard value={status.last_stats.cd_albums_no_cover ?? 0} label="CD albums no cover" />
              <StatCard value={status.last_stats.errors}     label="Errors" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
