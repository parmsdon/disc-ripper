import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const POLL_INTERVAL_MS = 3000;

const FILE_FILTERS = ["all", "pending", "error_retry", "already_imported", "skipped"];
const FILE_FILTER_LABEL = {
  all: "All",
  pending: "Pending",
  error_retry: "Error",
  already_imported: "Imported",
  skipped: "Skipped",
};

// Map import file status → status-pill CSS modifier (reuses existing pill classes)
const STATUS_PILL = {
  pending: "queued",
  already_imported: "done",
  error_retry: "error",
  skipped: "warn",
};
const STATUS_LABEL = {
  pending: "Pending",
  already_imported: "Imported",
  error_retry: "Error",
  skipped: "Skipped",
};

function MatchCell({ match, title }) {
  if (match === "exact") {
    const display = title
      ? title.length > 36 ? title.slice(0, 36) + "…" : title
      : "Exact";
    return <span title={title || undefined}>{display}</span>;
  }
  if (match === "ambiguous") return <span className="text-dim">Ambiguous</span>;
  return <span className="text-dim">—</span>;
}

function SummaryPills({ files, totals, jobStatus }) {
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
      {files.length === 0 && (
        <span className="encoder-stat-pill idle">No files</span>
      )}
    </div>
  );
}

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
  const isRunning = jobStatus === "running";

  const pendingCount = files.filter(
    (f) => f.status === "pending" || f.status === "error_retry"
  ).length;

  // Filter buttons: only show filters that have at least one matching file
  const filterCounts = { all: files.length };
  for (const f of files) filterCounts[f.status] = (filterCounts[f.status] || 0) + 1;

  const filteredFiles =
    statusFilter === "all" ? files : files.filter((f) => f.status === statusFilter);

  return (
    <div>
      <div className="control-bar encoder-header">
        <div className="control-bar-group">
          <span className="control-bar-label">Legacy Import</span>

          {!isRunning && !confirming && (
            <button
              className={`ripping-toggle${pendingCount > 0 ? "" : ""}`}
              onClick={() => setConfirming(true)}
              disabled={pendingCount === 0 || starting}
              title={
                pendingCount === 0
                  ? "No pending files"
                  : `Import ${pendingCount} pending file${pendingCount !== 1 ? "s" : ""}`
              }
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
            <button onClick={handleStop} style={{ marginLeft: "0.25rem" }}>
              Stop
            </button>
          )}

          <button
            onClick={fetchPreview}
            disabled={isRunning}
            style={{ marginLeft: "0.5rem" }}
          >
            Refresh
          </button>
        </div>

        <SummaryPills files={files} totals={totals} jobStatus={jobStatus} />
      </div>

      {(fetchError || jobError) && (
        <div className="panel" style={{ marginBottom: "0.75rem", padding: "0.6rem 1rem" }}>
          <span className="status-pill error" style={{ marginRight: "0.5rem" }}>Error</span>
          <span className="text-dim">{fetchError || jobError}</span>
        </div>
      )}

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
                {FILE_FILTER_LABEL[f]}
                {f !== "all" && ` (${count})`}
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
                      >
                        ⚠
                      </span>
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
                  <td>
                    <MatchCell match={f.catalog_match} title={f.catalog_title} />
                  </td>
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
    </div>
  );
}
