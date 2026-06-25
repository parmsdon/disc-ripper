import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const SEARCH_DEBOUNCE_MS = 300;
const POLL_INTERVAL_MS = 3000;

export default function MyMovies() {
  const [entries, setEntries] = useState([]);
  const [loadingEntries, setLoadingEntries] = useState(true);
  const [entriesError, setEntriesError] = useState(null);
  const [search, setSearch] = useState("");

  const [syncStatus, setSyncStatus] = useState(null);
  const [triggering, setTriggering] = useState(false);
  const pollRef = useRef(null);

  const fetchEntries = useCallback((term) => {
    api.getCatalog(term)
      .then((data) => {
        setEntries(data);
        setEntriesError(null);
      })
      .catch((e) => setEntriesError(e.message))
      .finally(() => setLoadingEntries(false));
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => fetchEntries(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [search, fetchEntries]);

  const refreshSyncStatus = useCallback(() => {
    api.getSyncStatus().then(setSyncStatus).catch(() => {});
  }, []);

  useEffect(() => {
    refreshSyncStatus();
  }, [refreshSyncStatus]);

  useEffect(() => {
    if (!syncStatus?.running) {
      return;
    }
    pollRef.current = setInterval(() => {
      api.getSyncStatus().then((data) => {
        setSyncStatus(data);
        if (!data.running) {
          fetchEntries(search);
        }
      }).catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [syncStatus?.running, fetchEntries, search]);

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
  const progressPct = progress
    ? Math.round((progress.current / progress.total) * 100)
    : 0;

  return (
    <div>
      <div className="panel">
        <h2>My Movies Sync</h2>
        <div className="sync-status-row">
          <span className="control-bar-label">
            Last sync:{" "}
            {syncStatus?.last_run_at ? new Date(syncStatus.last_run_at).toLocaleString() : "never"}
          </span>
          <span className="control-bar-label">
            {entries.length} catalog {entries.length === 1 ? "entry" : "entries"}
            {search ? " (filtered)" : ""}
          </span>
          <button onClick={handleSyncNow} disabled={triggering || syncing}>
            {syncing ? "Syncing..." : "Sync Now"}
          </button>
        </div>
        {syncing && progress && (
          <div className="sync-progress-row">
            <div className="progress-bar-track">
              <div
                className="progress-bar-fill"
                style={{ width: `${progressPct}%` }}
              />
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
              : `Synced ${lastResult.synced} (${lastResult.inserted} new, ${lastResult.updated} updated, ` +
                `${lastResult.errors} errors) in ${lastResult.duration_seconds.toFixed(1)}s`}
          </div>
        )}
      </div>

      <div className="panel">
        <h2>Catalog</h2>
        <input
          type="text"
          placeholder="Search title..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="catalog-search-input"
        />
        {entriesError && <div className="empty-state">Error: {entriesError}</div>}
        {!entriesError && loadingEntries && <div className="empty-state">Loading...</div>}
        {!entriesError && !loadingEntries && entries.length === 0 && (
          <div className="empty-state">No catalog entries found.</div>
        )}
        {!entriesError && entries.length > 0 && (
          <table className="catalog-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Year</th>
                <th>IMDB ID</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>{entry.title}</td>
                  <td>{entry.year ?? ""}</td>
                  <td>{entry.imdb_id ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
