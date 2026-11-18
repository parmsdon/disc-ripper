import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const POLL_INTERVAL_MS = 3000;

function StatusBadge({ status }) {
  const map = {
    pending: { label: "Pending", cls: "badge-pending" },
    already_imported: { label: "Done", cls: "badge-done" },
    skipped: { label: "Skipped", cls: "badge-warn" },
    error_retry: { label: "Retry", cls: "badge-retry" },
    imported: { label: "Imported", cls: "badge-done" },
  };
  const { label, cls } = map[status] || { label: status, cls: "" };
  return <span className={`import-badge ${cls}`}>{label}</span>;
}

function MatchBadge({ match, title }) {
  if (match === "exact") {
    return (
      <span className="match-exact" title={title || undefined}>
        Exact{title ? `: "${title}"` : ""}
      </span>
    );
  }
  if (match === "ambiguous") return <span className="match-ambiguous">Ambiguous</span>;
  return <span className="match-none">No match</span>;
}

export default function Import() {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [starting, setStarting] = useState(false);
  const pollingRef = useRef(null);

  const fetchPreview = useCallback(async () => {
    try {
      const data = await api.getImportPreview();
      setPreview(data);
      setError(null);
      return data;
    } catch (err) {
      setError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPreview();
  }, [fetchPreview]);

  // Poll while job is running or stopping
  useEffect(() => {
    if (!preview) return;
    const isActive = preview.job_status === "running" || preview.job_status === "stopping";
    if (isActive && !pollingRef.current) {
      pollingRef.current = setInterval(fetchPreview, POLL_INTERVAL_MS);
    } else if (!isActive && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [preview?.job_status, fetchPreview]);

  async function handleStart() {
    setStarting(true);
    setConfirming(false);
    try {
      await api.startImport();
      await fetchPreview();
    } catch (err) {
      setError(err.message);
    } finally {
      setStarting(false);
    }
  }

  async function handleStop() {
    try {
      await api.stopImport();
      await fetchPreview();
    } catch (err) {
      setError(err.message);
    }
  }

  if (loading) return <div className="page-loading">Loading…</div>;
  if (error && !preview) return <div className="page-error">Error: {error}</div>;

  const files = preview?.files || [];
  const totals = preview?.totals || {};
  const jobStatus = preview?.job_status || "idle";
  const isRunning = jobStatus === "running" || jobStatus === "stopping";
  const isInterrupted = jobStatus === "interrupted";

  const pendingCount = files.filter(
    (f) => f.status === "pending" || f.status === "error_retry"
  ).length;

  return (
    <div className="page-content">
      <div className="page-header">
        <h1>Legacy ISO Import</h1>
        <p className="page-subtitle">
          Import ISO files from <code>dvd_store/legacy/</code>. Place files there named{" "}
          <code>Title [Year].iso</code> (optionally with trailing region code like{" "}
          <code>(R1)</code>).
        </p>
      </div>

      {error && (
        <div className="page-error" style={{ marginBottom: "1rem" }}>
          Warning: {error}
        </div>
      )}

      {/* Control bar */}
      <div className="control-bar">
        <div className="control-bar-group">
          {!isRunning && !confirming && (
            <button
              className="btn-primary"
              onClick={() => setConfirming(true)}
              disabled={pendingCount === 0 || starting}
            >
              Import all ({pendingCount} pending)
            </button>
          )}

          {confirming && (
            <>
              <span style={{ marginRight: "0.75rem" }}>
                Import {pendingCount} file{pendingCount !== 1 ? "s" : ""}? This cannot be
                undone.
              </span>
              <button className="btn-primary" onClick={handleStart} disabled={starting}>
                {starting ? "Starting…" : "Confirm"}
              </button>
              <button onClick={() => setConfirming(false)} style={{ marginLeft: "0.5rem" }}>
                Cancel
              </button>
            </>
          )}

          {isRunning && (
            <button onClick={handleStop} disabled={jobStatus === "stopping"}>
              {jobStatus === "stopping" ? "Stopping…" : "Stop"}
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

        {/* Job status indicator */}
        <div className="control-bar-group">
          {jobStatus !== "idle" && (
            <span className={`job-status-badge job-status-${jobStatus}`}>
              {jobStatus === "running" && "Running…"}
              {jobStatus === "stopping" && "Stopping…"}
              {jobStatus === "done" && "Done"}
              {jobStatus === "error" && "Error"}
              {jobStatus === "interrupted" && "Interrupted (stale)"}
            </span>
          )}
          {isRunning && totals.total > 0 && (
            <span className="text-dim" style={{ marginLeft: "0.75rem" }}>
              {totals.processed || 0} / {totals.total} — {totals.imported || 0} imported,{" "}
              {totals.skipped || 0} skipped
            </span>
          )}
          {(jobStatus === "done" || isInterrupted) && totals.total > 0 && (
            <span className="text-dim" style={{ marginLeft: "0.75rem" }}>
              {totals.imported || 0} imported, {totals.skipped || 0} skipped,{" "}
              {totals.already_imported || 0} already done (of {totals.total})
            </span>
          )}
        </div>
      </div>

      {/* Preview table */}
      {files.length === 0 ? (
        <div className="audit-section-clear">
          No ISO files found in <code>dvd_store/legacy/</code>. Add files there to begin.
        </div>
      ) : (
        <div className="import-table-wrap">
          <table className="import-table">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Working title</th>
                <th>Catalog match</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.filename} className={`import-row import-row-${f.status}`}>
                  <td className="import-filename">
                    {f.filename}
                    {f.duplicate_warning && (
                      <span
                        className="import-warn-inline"
                        title={f.duplicate_warning}
                      >
                        {" "}
                        ⚠
                      </span>
                    )}
                    {f.in_raw && (
                      <span
                        className="text-dim"
                        title="File was moved to raw/ on a previous attempt"
                      >
                        {" "}
                        (in raw/)
                      </span>
                    )}
                  </td>
                  <td className="import-wt">{f.working_title || "—"}</td>
                  <td>
                    <MatchBadge match={f.catalog_match} title={f.catalog_title} />
                  </td>
                  <td>
                    <StatusBadge status={f.status} />
                    {f.reason && (
                      <span className="text-dim import-reason"> {f.reason}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
