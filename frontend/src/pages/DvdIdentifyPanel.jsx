import React, { useEffect, useState } from "react";
import { api } from "../api/client";

const SEARCH_DEBOUNCE_MS = 300;

function CatalogRow({ entry, selected, onSelect }) {
  return (
    <div
      className={`catalog-result-row${selected ? " selected" : ""}`}
      onClick={() => onSelect(entry)}
    >
      <span className="catalog-result-title">{entry.title}</span>
      {(entry.year || entry.imdb_id) && (
        <span className="catalog-result-meta">
          {[entry.year, entry.imdb_id].filter(Boolean).join(" · ")}
        </span>
      )}
    </div>
  );
}

export default function DvdIdentifyPanel({ disc, onConfirm, onSkip }) {
  const [suggestions, setSuggestions] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState(null);

  // Auto-suggestions on mount
  useEffect(() => {
    if (!disc.temp_name) {
      setSuggestions([]);
      return;
    }
    api.getCatalogSuggestions(disc.temp_name)
      .then(setSuggestions)
      .catch(() => setSuggestions([]));
  }, [disc.id, disc.temp_name]);

  // Debounced catalog search — empty query returns the full unmatched list
  useEffect(() => {
    setSearchLoading(true);
    const handle = setTimeout(() => {
      api.searchCatalog(searchQuery)
        .then((data) => { setSearchResults(data); setSearchLoading(false); })
        .catch(() => { setSearchResults([]); setSearchLoading(false); });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [searchQuery]);

  // Close on Escape
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onSkip(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onSkip]);

  function handleSelect(entry) {
    setSelected(entry);
    setConfirmError(null);
  }

  async function handleConfirm() {
    if (!selected || confirming) return;
    setConfirming(true);
    setConfirmError(null);
    try {
      await api.identifyDvd(disc.id, selected.id);
      onConfirm();
    } catch (e) {
      if (e.message.startsWith("API error 409")) {
        setConfirmError("This title is already matched to another disc");
        setSelected(null);
      } else {
        setConfirmError(e.message);
      }
      setConfirming(false);
    }
  }

  return (
    <div className="identify-panel-overlay" onClick={onSkip}>
      <div className="identify-panel dvd-identify-panel" onClick={(e) => e.stopPropagation()}>

        <div className="identify-panel-header">
          <div>
            <div className="identify-panel-title">Identify DVD</div>
            <div className="identify-disc-name">{disc.temp_name || "Unnamed"}</div>
            {disc.disc_fingerprint && (
              <div className="identify-disc-fp">{disc.disc_fingerprint}</div>
            )}
          </div>
          <button className="mb-popover-close" onClick={onSkip}>×</button>
        </div>

        <div className="identify-panel-body">

          <div className="identify-section">
            <div className="identify-section-label">Suggested matches</div>
            <div className="suggestion-cards">
              {suggestions === null || suggestions.length === 0 ? (
                <div className="suggestions-hint">
                  {suggestions === null
                    ? "Loading suggestions…"
                    : "No automatic suggestions — please search below"}
                </div>
              ) : (
                suggestions.map((entry) => (
                  <div
                    key={entry.id}
                    className={`suggestion-card${selected?.id === entry.id ? " selected" : ""}`}
                    onClick={() => handleSelect(entry)}
                  >
                    <div className="suggestion-card-title">{entry.title}</div>
                    {(entry.year || entry.imdb_id) && (
                      <div className="suggestion-card-meta">
                        {[entry.year, entry.imdb_id].filter(Boolean).join(" · ")}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="identify-section">
            <div className="identify-section-label">Search catalog</div>
            <input
              type="text"
              className="catalog-search-input"
              placeholder="Search My Movies catalog…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            <div className="catalog-search-results">
              {searchLoading && (
                <div className="empty-state">Searching…</div>
              )}
              {!searchLoading && searchResults !== null && searchResults.length === 0 && (
                <div className="empty-state">No matches in My Movies catalog</div>
              )}
              {!searchLoading && searchResults && searchResults.map((entry) => (
                <CatalogRow
                  key={entry.id}
                  entry={entry}
                  selected={selected?.id === entry.id}
                  onSelect={handleSelect}
                />
              ))}
            </div>
          </div>

          {confirmError && (
            <div className="identify-error">{confirmError}</div>
          )}

        </div>

        <div className="identify-panel-footer">
          <button onClick={handleConfirm} disabled={!selected || confirming}>
            {confirming ? "Confirming…" : "Confirm"}
          </button>
          <button onClick={onSkip} disabled={confirming}>Skip</button>
          {selected && !confirming && (
            <span className="identify-selection-label">
              {selected.title}{selected.year ? ` (${selected.year})` : ""}
            </span>
          )}
        </div>

      </div>
    </div>
  );
}
