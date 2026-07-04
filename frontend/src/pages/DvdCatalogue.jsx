import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const SEARCH_DEBOUNCE_MS = 300;
const POLL_INTERVAL_MS = 3000;

function formatDate(isoStr) {
  if (!isoStr) return null;
  const d = new Date(isoStr);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function RowTypeBadge({ type }) {
  if (type === "matched")      return <span className="status-pill good">Matched</span>;
  if (type === "unripped")     return <span className="status-pill running">Unripped</span>;
  if (type === "unmatched_rip") return <span className="status-pill imperfect">Unmatched Rip</span>;
  return null;
}

export default function DvdCatalogue() {
  const [syncStatus, setSyncStatus] = useState(null);
  const [triggering, setTriggering] = useState(false);
  const pollRef = useRef(null);

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [ripStatus, setRipStatus] = useState(null);   // null | "ripped" | "unripped"
  const [idStatus, setIdStatus] = useState(null);     // null | "identified" | "unidentified"
  const [mmStatus, setMmStatus] = useState(null);     // null | "matched" | "unmatched"
  const [dirty, setDirty] = useState(false);
  const [search, setSearch] = useState("");

  const fetchRows = useCallback((rs, is, ms, d, s) => {
    api.getDvdCatalogue({ ripStatus: rs, idStatus: is, mmStatus: ms, dirty: d, search: s })
      .then((data) => { setRows(data); setLoadError(null); })
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => fetchRows(ripStatus, idStatus, mmStatus, dirty, search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [ripStatus, idStatus, mmStatus, dirty, search, fetchRows]);

  const refreshSyncStatus = useCallback(() => {
    api.getSyncStatus().then(setSyncStatus).catch(() => {});
  }, []);

  useEffect(() => { refreshSyncStatus(); }, [refreshSyncStatus]);

  useEffect(() => {
    if (!syncStatus?.running) return;
    pollRef.current = setInterval(() => {
      api.getSyncStatus().then((data) => {
        setSyncStatus(data);
        if (!data.running) fetchRows(ripStatus, idStatus, mmStatus, dirty, search);
      }).catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [syncStatus?.running, fetchRows, ripStatus, idStatus, mmStatus, dirty, search]);

  async function handleDelete(discId) {
    if (!window.confirm("Delete this unnamed disc record?")) return;
    setDeleteError(null);
    try {
      await api.deleteDisc(discId);
      fetchRows(ripStatus, idStatus, mmStatus, dirty, search);
    } catch (e) {
      setDeleteError(e.message);
    }
  }

  async function handleSyncNow() {
    setTriggering(true);
    try {
      const data = await api.triggerSync();
      if (data.status === "started") {
        setSyncStatus((s) => ({ ...(s || {}), running: true, progress: null }));
      }
      refreshSyncStatus();
    } finally {
      setTriggering(false);
    }
  }

  const syncing = Boolean(syncStatus?.running);
  const lastResult = syncStatus?.last_result;
  const progress = syncing ? syncStatus?.progress : null;
  const progressPct = progress ? Math.round((progress.current / progress.total) * 100) : 0;

  return (
    <div>
      <div className="panel">
        <h2>My Movies Sync</h2>
        <div className="sync-status-row">
          <span className="control-bar-label">
            Last sync:{" "}
            {syncStatus?.last_run_at
              ? new Date(syncStatus.last_run_at).toLocaleString()
              : "never"}
          </span>
          <button onClick={handleSyncNow} disabled={triggering || syncing}>
            {syncing ? "Syncing..." : "Sync Now"}
          </button>
        </div>
        {syncing && progress && (
          <div className="sync-progress-row">
            <div className="progress-bar-track">
              <div className="progress-bar-fill" style={{ width: `${progressPct}%` }} />
            </div>
            <span className="progress-bar-label">
              {progress.current} / {progress.total}
            </span>
          </div>
        )}
        {lastResult && !syncing && (
          <div className="sync-result-line">
            {lastResult.error
              ? `Last sync failed: ${lastResult.error}`
              : `Synced ${lastResult.synced} (${lastResult.inserted} new, ` +
                `${lastResult.updated} updated, ${lastResult.errors} errors) ` +
                `in ${lastResult.duration_seconds.toFixed(1)}s`}
          </div>
        )}
      </div>

      <div className="panel">
        <h2>DVD Catalogue</h2>
        <div className="catalogue-toolbar">
          <div className="catalogue-filter-groups">
            <div className="catalogue-filters">
              {[["ripped", "Ripped"], ["unripped", "Unripped"]].map(([key, label]) => (
                <button
                  key={key}
                  className={`catalogue-filter-btn${ripStatus === key ? " active" : ""}`}
                  onClick={() => { setRipStatus((v) => (v === key ? null : key)); setLoading(true); }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="catalogue-filters">
              {[["identified", "Identified"], ["unidentified", "Unidentified"]].map(([key, label]) => (
                <button
                  key={key}
                  className={`catalogue-filter-btn${idStatus === key ? " active" : ""}`}
                  onClick={() => { setIdStatus((v) => (v === key ? null : key)); setLoading(true); }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="catalogue-filter-group-labeled">
              <span className="catalogue-filter-group-label">My Movies:</span>
              <div className="catalogue-filters">
                {[["matched", "Matched"], ["unmatched", "Unmatched"]].map(([key, label]) => (
                  <button
                    key={key}
                    className={`catalogue-filter-btn${mmStatus === key ? " active" : ""}`}
                    onClick={() => { setMmStatus((v) => (v === key ? null : key)); setLoading(true); }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <button
            className={`catalogue-filter-btn catalogue-filter-btn--warn${dirty ? " active" : ""}`}
            onClick={() => { setDirty((v) => !v); setLoading(true); }}
          >
            Dirty
          </button>
          <input
            type="text"
            className="catalog-search-input"
            placeholder="Search title, name, fingerprint…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setLoading(true); }}
          />
        </div>

        <div className="catalogue-count">
          {loading
            ? "Loading…"
            : `Showing ${rows.length} ${rows.length === 1 ? "entry" : "entries"}`}
        </div>

        {loadError && <div className="catalogue-empty">Error: {loadError}</div>}
        {deleteError && <div className="catalogue-empty" style={{ color: "var(--error)" }}>Delete failed: {deleteError}</div>}
        {!loadError && !loading && rows.length === 0 && (
          <div className="catalogue-empty">No entries found.</div>
        )}
        {!loadError && rows.length > 0 && (
          <table className="catalogue-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>My Movies</th>
                <th>DVD</th>
                <th>Ripped</th>
                <th>Quality</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={`${row.row_type}-${row.catalog_id ?? "nc"}-${row.disc_id ?? i}`}
                  className="catalogue-row"
                >
                  <td><RowTypeBadge type={row.row_type} /></td>
                  <td>
                    {row.catalog_title ? (
                      <>
                        <div>{row.catalog_title}</div>
                        <div className="catalogue-meta">
                          {[row.catalog_year, row.catalog_imdb_id].filter(Boolean).join(" · ")}
                        </div>
                      </>
                    ) : <span className="catalogue-dim">—</span>}
                  </td>
                  <td>
                    {row.disc_id == null ? (
                      <span className="catalogue-dim">—</span>
                    ) : (
                      <>
                        <div>
                          {row.disc_temp_name || <em className="catalogue-dim">Unnamed</em>}
                        </div>
                        {row.disc_fingerprint && (
                          <div className="catalogue-meta catalogue-mono">
                            {row.disc_fingerprint}
                          </div>
                        )}
                      </>
                    )}
                  </td>
                  <td>
                    {row.disc_ripped_at
                      ? formatDate(row.disc_ripped_at)
                      : <span className="catalogue-dim">—</span>}
                  </td>
                  <td>
                    {row.disc_rip_quality === "dirty"
                      ? <span className="dirty-rip-badge">⚠ dirty</span>
                      : <span className="catalogue-dim">—</span>}
                  </td>
                  <td>
                    {row.row_type === "unmatched_rip" && !row.disc_temp_name && (
                      <button
                        className="catalogue-delete-btn"
                        onClick={() => handleDelete(row.disc_id)}
                        title="Delete this unnamed disc record"
                      >
                        ✕
                      </button>
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
