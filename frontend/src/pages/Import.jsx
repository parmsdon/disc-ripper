import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";

const POLL_INTERVAL_MS = 3000;
const DISCS_POLL_MS = 10000;

// ── File table ────────────────────────────────────────────────────────────────

const FILE_FILTERS = ["all", "pending", "error_retry", "already_imported", "skipped"];
const FILE_FILTER_LABEL = {
  all: "All", pending: "Pending", error_retry: "Error",
  already_imported: "Imported", skipped: "Skipped",
};
const STATUS_PILL = {
  pending: "queued", already_imported: "done", error_retry: "error", skipped: "warn",
};
const STATUS_LABEL = {
  pending: "Pending", already_imported: "Imported", error_retry: "Error", skipped: "Skipped",
};

function MatchCell({ match, title }) {
  if (match === "exact") {
    const display = title
      ? (title.length > 36 ? title.slice(0, 36) + "…" : title)
      : "Exact";
    return <span title={title || undefined}>{display}</span>;
  }
  if (match === "ambiguous") return <span className="text-dim">Ambiguous</span>;
  return <span className="text-dim">—</span>;
}

function FileSummaryPills({ files, totals, jobStatus }) {
  const counts = {};
  for (const f of files) counts[f.status] = (counts[f.status] || 0) + 1;
  return (
    <div className="encoder-stats">
      {jobStatus === "running" && (totals.total || 0) > 0 && (
        <span className="encoder-stat-pill running">
          {totals.processed || 0} / {totals.total}
        </span>
      )}
      {(counts.pending || 0) > 0 && (
        <span className="encoder-stat-pill queued">{counts.pending} pending</span>
      )}
      {(counts.already_imported || 0) > 0 && (
        <span className="encoder-stat-pill done">{counts.already_imported} imported</span>
      )}
      {(counts.error_retry || 0) > 0 && (
        <span className="encoder-stat-pill error">
          {counts.error_retry} error{counts.error_retry !== 1 ? "s" : ""}
        </span>
      )}
      {(counts.skipped || 0) > 0 && (
        <span className="encoder-stat-pill idle">{counts.skipped} skipped</span>
      )}
      {files.length === 0 && <span className="encoder-stat-pill idle">No files</span>}
    </div>
  );
}

// ── Last run banner ───────────────────────────────────────────────────────────

function fmtDatetime(iso) {
  if (!iso) return "—";
  const d = new Date(iso + (iso.includes("Z") || iso.includes("+") ? "" : "Z"));
  return d.toLocaleString([], {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtElapsed(startIso, nowMs) {
  if (!startIso) return "";
  const secs = Math.round(
    (nowMs - new Date(startIso + (startIso.includes("Z") || startIso.includes("+") ? "" : "Z")).getTime()) / 1000
  );
  if (secs < 0) return "";
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60), s = secs % 60;
  if (m < 60) return `${m}m ${s}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function fmtDuration(startIso, endIso) {
  if (!startIso || !endIso) return null;
  const ms =
    new Date(endIso + (endIso.includes("Z") ? "" : "Z")) -
    new Date(startIso + (startIso.includes("Z") ? "" : "Z"));
  if (ms < 0) return null;
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

const OUTCOME_PILL = {
  done: "done",
  stopped: "warn",
  interrupted: "warn",
  error: "error",
};
const OUTCOME_LABEL = {
  done: "Done",
  stopped: "Stopped",
  interrupted: "Interrupted",
  error: "Error",
};

function LastRunBanner({ lastRun, totals, jobStatus }) {
  const [tick, setTick] = useState(Date.now());
  const isRunning = jobStatus === "running";

  useEffect(() => {
    if (!isRunning) return;
    const timer = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isRunning]);

  if (!lastRun) return null;

  if (isRunning) {
    const processed = totals?.processed || 0;
    const total = totals?.total || 0;
    const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
    const currentFile = totals?.current_file || null;
    return (
      <div className="panel" style={{ marginBottom: "0.75rem", padding: "0.75rem 1rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.5rem" }}>
          <span className="text-dim" style={{ fontSize: "0.82rem" }}>In progress</span>
          <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
            {processed} of {total}
          </span>
          {lastRun.started_at && (
            <span className="text-dim" style={{ fontSize: "0.82rem", marginLeft: "auto" }}>
              {fmtElapsed(lastRun.started_at, tick)}
            </span>
          )}
        </div>
        {total > 0 && (
          <div className="lib-progress-track" style={{ marginBottom: currentFile ? "0.4rem" : 0 }}>
            <div className="lib-progress-fill" style={{ width: `${pct}%` }} />
          </div>
        )}
        {currentFile && (
          <div className="lib-progress-step">{currentFile}</div>
        )}
      </div>
    );
  }

  const { started_at, finished_at, outcome, imported, skipped, already_imported } = lastRun;
  const dur = fmtDuration(started_at, finished_at);
  const pillClass = OUTCOME_PILL[outcome] || "idle";
  const parts = [];
  if (imported > 0) parts.push(`${imported} imported`);
  if (skipped > 0) parts.push(`${skipped} skipped`);
  if (already_imported > 0) parts.push(`${already_imported} already done`);
  return (
    <div className="panel" style={{ marginBottom: "0.75rem", padding: "0.55rem 1rem", display: "flex", alignItems: "center", gap: "0.75rem" }}>
      <span className="text-dim" style={{ fontSize: "0.82rem" }}>Last run</span>
      <span style={{ fontSize: "0.85rem" }}>{fmtDatetime(started_at)}</span>
      {finished_at && (
        <>
          <span className="text-dim">→</span>
          <span style={{ fontSize: "0.85rem" }}>{fmtDatetime(finished_at)}</span>
          {dur && <span className="text-dim" style={{ fontSize: "0.82rem" }}>({dur})</span>}
        </>
      )}
      {parts.length > 0 && (
        <span className="text-dim" style={{ fontSize: "0.85rem" }}>· {parts.join(", ")}</span>
      )}
      {outcome && (
        <span className={`status-pill ${pillClass}`} style={{ marginLeft: "auto" }}>
          {OUTCOME_LABEL[outcome] || outcome}
        </span>
      )}
    </div>
  );
}

// ── Imported discs section ────────────────────────────────────────────────────

const DISC_FILTERS = ["all", "needs_id", "error"];
const DISC_FILTER_LABEL = {
  all: "All", needs_id: "Needs ID", error: "Error",
};

function CatalogCell({ catalogId, catalogTitle, source, collectionNumber, edition, duplicateRole }) {
  if (!catalogId) {
    return (
      <Link to="/data-editing" style={{ fontSize: "0.85rem" }}>
        <span className="status-pill warn">Needs ID</span>
      </Link>
    );
  }
  const prefix = source === "auto" ? "Auto" : source === "manual" ? "Manual" : null;
  const shortTitle = catalogTitle
    ? (catalogTitle.length > 28 ? catalogTitle.slice(0, 28) + "…" : catalogTitle)
    : null;
  return (
    <span>
      {prefix && (
        <span className="text-dim" style={{ fontSize: "0.78rem", marginRight: "0.3rem" }}>
          {prefix}:
        </span>
      )}
      {shortTitle || <span className="text-dim">#{catalogId}</span>}
      {duplicateRole === "primary" && (
        <span className="dup-badge dup-badge--primary" style={{ marginLeft: "0.3rem" }}>1°</span>
      )}
      {duplicateRole === "secondary" && (
        <span className="dup-badge dup-badge--secondary" style={{ marginLeft: "0.3rem" }}>2°</span>
      )}
      {(collectionNumber != null || edition) && (
        <span className="text-dim" style={{ fontSize: "0.78rem", display: "block", marginTop: "0.1rem" }}>
          {[collectionNumber != null ? `#${collectionNumber}` : null, edition].filter(Boolean).join(" · ")}
        </span>
      )}
    </span>
  );
}


function ImportedDiscsSection() {
  const [data, setData] = useState(null);
  const [discFilter, setDiscFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchDiscs = useCallback(async (f = discFilter) => {
    try {
      const result = await api.getImportedDiscs({ filter: f });
      setData(result);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [discFilter]);

  useEffect(() => {
    setLoading(true);
    fetchDiscs(discFilter);
  }, [discFilter]);

  useEffect(() => {
    const iv = setInterval(() => fetchDiscs(discFilter), DISCS_POLL_MS);
    return () => clearInterval(iv);
  }, [fetchDiscs, discFilter]);

  const counts = data?.counts || {};
  const discs = data?.discs || [];

  return (
    <div className="panel" style={{ marginTop: "0.75rem" }}>
      <div className="panel-heading-row" style={{ padding: "0.75rem 1rem 0" }}>
        <h2>Imported discs</h2>
        {data && (
          <span className="text-dim" style={{ fontSize: "0.85rem" }}>
            {counts.all || 0} total
          </span>
        )}
      </div>

      <div className="encoder-filter-row">
        {DISC_FILTERS.map((f) => {
          const cnt = f === "all" ? (counts.all || 0) : (counts[f] || 0);
          if (f !== "all" && cnt === 0) return null;
          return (
            <button
              key={f}
              className={`catalogue-filter-btn${discFilter === f ? " active" : ""}`}
              onClick={() => setDiscFilter(f)}
            >
              {DISC_FILTER_LABEL[f]}{f !== "all" && ` (${cnt})`}
            </button>
          );
        })}
      </div>

      {loading && discs.length === 0 ? (
        <div className="empty-state">Loading…</div>
      ) : error ? (
        <div className="empty-state">Error: {error}</div>
      ) : discs.length === 0 ? (
        <div className="empty-state">
          {discFilter === "all"
            ? "No imported discs yet. Run an import to see them here."
            : "No discs match this filter."}
        </div>
      ) : (
        <table className="catalogue-table">
          <colgroup>
            <col />
            <col style={{ width: "190px" }} />
            <col style={{ width: "210px" }} />
          </colgroup>
          <thead>
            <tr>
              <th>Filename</th>
              <th>Working title</th>
              <th>Catalog</th>
            </tr>
          </thead>
          <tbody>
            {discs.map((d) => (
              <tr key={d.id} className="catalogue-row">
                <td>
                  <span className="encoder-disc-name">
                    {d.imported_filename || <span className="text-dim">#{d.id}</span>}
                  </span>
                  {d.disc_status === "error" && d.error_message && (
                    <div
                      className="text-dim"
                      title={d.error_message}
                      style={{ fontSize: "0.75rem", marginTop: "0.15rem", color: "var(--error)" }}
                    >
                      {d.error_message.length > 60
                        ? d.error_message.slice(0, 60) + "…"
                        : d.error_message}
                    </div>
                  )}
                </td>
                <td>
                  <span className="text-dim" style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>
                    {d.temp_name || "—"}
                  </span>
                </td>
                <td>
                  <CatalogCell
                    catalogId={d.catalog_id}
                    catalogTitle={d.catalog_title}
                    source={d.catalog_match_source}
                    collectionNumber={d.catalog_collection_number}
                    edition={d.catalog_edition}
                    duplicateRole={d.catalog_duplicate_role}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Import() {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");
  const pollingRef = useRef(null);

  const fetchPreview = useCallback(async () => {
    try {
      const data = await api.getImportPreview();
      setPreview(data);
      setFetchError(null);
      return data;
    } catch (err) {
      setFetchError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchPreview(); }, [fetchPreview]);

  useEffect(() => {
    if (!preview) return;
    const isActive = preview.job_status === "running";
    if (isActive && !pollingRef.current) {
      pollingRef.current = setInterval(fetchPreview, POLL_INTERVAL_MS);
    } else if (!isActive && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    return () => {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null; }
    };
  }, [preview?.job_status, fetchPreview]);

  async function handleStart() {
    setStarting(true);
    setConfirming(false);
    try {
      await api.startImport();
      await fetchPreview();
    } catch (err) {
      setFetchError(err.message || "Failed to start import");
    } finally {
      setStarting(false);
    }
  }

  async function handleStop() {
    try {
      await api.stopImport();
      await fetchPreview();
    } catch (err) {
      setFetchError(err.message);
    }
  }

  if (loading) return <div className="empty-state">Loading…</div>;

  const files = preview?.files || [];
  const totals = preview?.totals || {};
  const jobStatus = preview?.job_status || "idle";
  const jobError = preview?.job_error || null;
  const lastRun = preview?.last_run || null;
  const isRunning = jobStatus === "running";

  const pendingCount = files.filter(
    (f) => f.status === "pending" || f.status === "error_retry"
  ).length;

  const filterCounts = { all: files.length };
  for (const f of files) filterCounts[f.status] = (filterCounts[f.status] || 0) + 1;

  const filteredFiles =
    statusFilter === "all" ? files : files.filter((f) => f.status === statusFilter);

  return (
    <div>
      {/* Control bar */}
      <div className="control-bar encoder-header">
        <div className="control-bar-group">
          <span className="control-bar-label">Legacy Import</span>

          {!isRunning && !confirming && (
            <button
              className="ripping-toggle"
              onClick={() => setConfirming(true)}
              disabled={pendingCount === 0 || starting}
              title={pendingCount === 0 ? "No pending files" : undefined}
            >
              Import all ({pendingCount})
            </button>
          )}

          {confirming && (
            <>
              <span className="text-dim" style={{ marginRight: "0.75rem" }}>
                Import {pendingCount} pending file{pendingCount !== 1 ? "s" : ""}?
              </span>
              <button
                className="ripping-toggle active"
                onClick={handleStart}
                disabled={starting}
              >
                {starting ? "Starting…" : "Confirm"}
              </button>
              <button onClick={() => setConfirming(false)} style={{ marginLeft: "0.5rem" }}>
                Cancel
              </button>
            </>
          )}

          {isRunning && (
            <button onClick={handleStop} style={{ marginLeft: "0.25rem" }}>Stop</button>
          )}

          <button onClick={fetchPreview} disabled={isRunning} style={{ marginLeft: "0.5rem" }}>
            Refresh
          </button>
        </div>

        <FileSummaryPills files={files} totals={totals} jobStatus={jobStatus} />
      </div>

      {/* Error */}
      {(fetchError || jobError) && (
        <div className="panel" style={{ marginBottom: "0.75rem", padding: "0.6rem 1rem" }}>
          <span className="status-pill error" style={{ marginRight: "0.5rem" }}>Error</span>
          <span className="text-dim">{fetchError || jobError}</span>
        </div>
      )}

      {/* Last run banner */}
      <LastRunBanner lastRun={lastRun} totals={totals} jobStatus={jobStatus} />

      {/* Live file table */}
      <div className="panel">
        <div className="encoder-filter-row">
          {FILE_FILTERS.map((f) => {
            const count = filterCounts[f] || 0;
            if (f !== "all" && count === 0) return null;
            return (
              <button
                key={f}
                className={`catalogue-filter-btn${statusFilter === f ? " active" : ""}`}
                onClick={() => setStatusFilter(f)}
              >
                {FILE_FILTER_LABEL[f]}{f !== "all" && ` (${count})`}
              </button>
            );
          })}
        </div>

        {files.length === 0 ? (
          <div className="empty-state">
            No ISO files in <code>dvd_store/legacy/</code>. Place files there named{" "}
            <code>Title [Year].iso</code> (optionally with trailing region code like{" "}
            <code>(R1)</code>).
          </div>
        ) : filteredFiles.length === 0 ? (
          <div className="empty-state">No files match this filter.</div>
        ) : (
          <table className="catalogue-table">
            <colgroup>
              <col />
              <col style={{ width: "220px" }} />
              <col style={{ width: "190px" }} />
              <col style={{ width: "120px" }} />
            </colgroup>
            <thead>
              <tr>
                <th>Filename</th>
                <th>Working title</th>
                <th>Catalog match</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredFiles.map((f) => (
                <tr key={f.filename} className="catalogue-row">
                  <td>
                    <span className="encoder-disc-name">{f.filename}</span>
                    {f.duplicate_warning && (
                      <span
                        className="error-hint"
                        title={f.duplicate_warning}
                        style={{ marginLeft: "0.4rem" }}
                      >⚠</span>
                    )}
                    {f.in_raw && (
                      <span
                        className="text-dim"
                        title="File was moved to raw/ on a previous attempt"
                      >
                        {" "}(in raw/)
                      </span>
                    )}
                  </td>
                  <td>
                    <span
                      className="text-dim"
                      style={{ fontFamily: "monospace", fontSize: "0.78rem" }}
                    >
                      {f.working_title || "—"}
                    </span>
                  </td>
                  <td><MatchCell match={f.catalog_match} title={f.catalog_title} /></td>
                  <td>
                    <span className={`status-pill ${STATUS_PILL[f.status] || f.status}`}>
                      {STATUS_LABEL[f.status] || f.status}
                    </span>
                    {f.reason && (
                      <span
                        className="error-hint"
                        title={f.reason}
                        style={{ marginLeft: "0.4rem", fontSize: "0.75rem" }}
                      >
                        {f.reason.length > 50 ? f.reason.slice(0, 50) + "…" : f.reason}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Imported discs history */}
      <ImportedDiscsSection />
    </div>
  );
}
