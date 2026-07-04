import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

const SEARCH_DEBOUNCE_MS = 300;

const FILTERS = [
  { key: "all", label: "All" },
  { key: "identified", label: "Identified" },
  { key: "unidentified", label: "Unidentified" },
  { key: "no_mb_match", label: "No MB Match" },
  { key: "mb_pending_error", label: "MB Issue" },
];

function formatDate(isoStr) {
  if (!isoStr) return null;
  const d = new Date(isoStr);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function CdStatusBadge({ row }) {
  if (row.identified)                          return <span className="status-pill done">Identified</span>;
  if (row.mb_lookup_status === "not_found")    return <span className="status-pill error">No Match</span>;
  if (row.mb_lookup_status === "pending")      return <span className="status-pill running">Pending</span>;
  if (row.mb_lookup_status === "error")        return <span className="status-pill error">MB Error</span>;
  return <span className="status-pill queued">Unidentified</span>;
}

function MbCell({ row }) {
  if (row.mb_lookup_status === "found") {
    const n = row.mb_candidate_count;
    return <span>{n} candidate{n !== 1 ? "s" : ""}</span>;
  }
  if (row.mb_lookup_status === "not_found") return <span className="catalogue-dim">No match</span>;
  if (row.mb_lookup_status === "pending")   return <span className="catalogue-dim">Pending…</span>;
  if (row.mb_lookup_status === "error")     return <span className="catalogue-dim">Error</span>;
  return <span className="catalogue-dim">—</span>;
}

export default function CdCatalogue() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [deleteError, setDeleteError] = useState(null);
  const [filter, setFilter] = useState("all");
  const [dirty, setDirty] = useState(false);
  const [search, setSearch] = useState("");

  const fetchRows = useCallback((f, d, s) => {
    api.getCdCatalogue(f, s, d)
      .then((data) => { setRows(data); setLoadError(null); })
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => fetchRows(filter, dirty, search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [filter, dirty, search, fetchRows]);

  async function handleDelete(discId) {
    setDeleteError(null);
    try {
      await api.deleteDisc(discId);
      fetchRows(filter, dirty, search);
    } catch (e) {
      setDeleteError(e.message);
    }
  }

  return (
    <div>
      <div className="panel">
        <h2>CD Catalogue</h2>
        <div className="catalogue-toolbar">
          <div className="catalogue-filters">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                className={`catalogue-filter-btn${filter === f.key ? " active" : ""}`}
                onClick={() => { setFilter(f.key); setLoading(true); }}
              >
                {f.label}
              </button>
            ))}
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
            placeholder="Search album, artist, fingerprint…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setLoading(true); }}
          />
        </div>

        <div className="catalogue-count">
          {loading
            ? "Loading…"
            : `Showing ${rows.length} ${rows.length === 1 ? "disc" : "discs"}`}
        </div>

        {loadError && <div className="catalogue-empty">Error: {loadError}</div>}
        {deleteError && <div className="catalogue-empty" style={{ color: "var(--error)" }}>Delete failed: {deleteError}</div>}
        {!loadError && !loading && rows.length === 0 && (
          <div className="catalogue-empty">No discs found.</div>
        )}
        {!loadError && rows.length > 0 && (
          <table className="catalogue-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>CD</th>
                <th>Album</th>
                <th>Tracks</th>
                <th>MusicBrainz</th>
                <th>Ripped</th>
                <th>Quality</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const tracksMissing = row.track_count > 0 && row.titled_tracks < row.track_count;
                return (
                  <tr key={row.disc_id} className="catalogue-row">
                    <td><CdStatusBadge row={row} /></td>
                    <td>
                      {row.disc_temp_name && <div>{row.disc_temp_name}</div>}
                      {row.disc_fingerprint && (
                        <div className="catalogue-meta catalogue-mono">{row.disc_fingerprint}</div>
                      )}
                    </td>
                    <td>
                      {row.album_title ? (
                        <>
                          <div>{row.album_title}</div>
                          {row.album_artist && (
                            <div className="catalogue-meta">{row.album_artist}</div>
                          )}
                        </>
                      ) : <span className="catalogue-dim">—</span>}
                    </td>
                    <td>
                      <span className={tracksMissing ? "catalogue-warn" : ""}>
                        {row.titled_tracks} / {row.track_count} titled
                      </span>
                    </td>
                    <td><MbCell row={row} /></td>
                    <td>
                      {row.disc_ripped_at ? (
                        <>
                          <div>{formatDate(row.disc_ripped_at)}</div>
                          {row.disc_rip_attempt_count > 1 && (
                            <div className="catalogue-meta">
                              {row.disc_rip_attempt_count} attempts
                            </div>
                          )}
                        </>
                      ) : <span className="catalogue-dim">—</span>}
                    </td>
                    <td>
                      {row.disc_rip_quality === "dirty"
                        ? <span className="dirty-rip-badge">⚠ dirty</span>
                        : <span className="catalogue-dim">—</span>}
                    </td>
                    <td>
                      {row.disc_id && !row.disc_temp_name && !row.album_title && (
                        <button
                          className="catalogue-delete-btn"
                          onClick={() => handleDelete(row.disc_id)}
                          title="Delete this unidentified disc record"
                        >
                          ✕
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
