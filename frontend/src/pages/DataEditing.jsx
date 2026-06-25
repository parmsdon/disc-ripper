import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import DvdIdentifyPanel from "./DvdIdentifyPanel.jsx";

const POLL_INTERVAL_MS = 30000;

function formatRippedAt(isoStr) {
  if (!isoStr) return null;
  const d = new Date(isoStr);
  const date = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${date} ${time}`;
}

function MbStatusBadge({ disc }) {
  if (disc.type !== "cd" || !disc.mb_lookup_status) return null;

  if (disc.mb_lookup_status === "pending") {
    return <span className="mb-queue-status pending">Looking up…</span>;
  }
  if (disc.mb_lookup_status === "found") {
    const n = disc.candidate_count;
    return (
      <span className="mb-queue-status found">
        {n} match{n !== 1 ? "es" : ""}
      </span>
    );
  }
  if (disc.mb_lookup_status === "not_found") {
    return <span className="mb-queue-status not-found">No matches</span>;
  }
  return null;
}

function QueueEntry({ disc, onIdentify }) {
  const rippedAt = formatRippedAt(disc.ripped_at);
  return (
    <div className="queue-entry">
      <span className={`queue-entry-type-badge ${disc.type}`}>
        {disc.type.toUpperCase()}
      </span>
      <div className="queue-entry-info">
        <div className="queue-entry-title">
          {disc.temp_name
            ? disc.temp_name
            : <em className="queue-entry-unnamed">Unnamed</em>
          }
        </div>
        {disc.disc_fingerprint && (
          <div className="queue-entry-meta">{disc.disc_fingerprint}</div>
        )}
        {rippedAt && (
          <div className="queue-entry-date">Ripped: {rippedAt}</div>
        )}
      </div>
      <div className="queue-entry-badges">
        <MbStatusBadge disc={disc} />
        {disc.rip_quality === "dirty" && (
          <span className="dirty-rip-badge">⚠ dirty</span>
        )}
      </div>
      <button onClick={() => onIdentify(disc)}>Identify</button>
    </div>
  );
}

// Placeholder for CDs — replaced in the next prompt.
function CdIdentifyPlaceholder({ disc, onClose }) {
  return (
    <div className="identify-panel-overlay" onClick={onClose}>
      <div className="identify-panel" onClick={(e) => e.stopPropagation()}>
        <div className="identify-panel-header">
          <span>CD — {disc.temp_name || "Unnamed"}</span>
          <button className="mb-popover-close" onClick={onClose}>×</button>
        </div>
        <div style={{ padding: "24px", textAlign: "center", color: "var(--text-dim)" }}>
          CD identification panel coming soon.
        </div>
        <div style={{ padding: "0 24px 24px", textAlign: "center" }}>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

export default function DataEditing() {
  const [queue, setQueue] = useState(null);
  const [error, setError] = useState(null);
  const [selectedDisc, setSelectedDisc] = useState(null);

  const fetchQueue = useCallback(() => {
    api.getIdentificationQueue()
      .then((data) => { setQueue(data); setError(null); })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    fetchQueue();
    const interval = setInterval(fetchQueue, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchQueue]);

  if (error) {
    return (
      <div className="panel">
        <h2>Data Editing</h2>
        <div className="empty-state">Error loading queue: {error}</div>
      </div>
    );
  }

  if (queue === null) {
    return (
      <div className="panel">
        <h2>Data Editing</h2>
        <div className="empty-state">Loading…</div>
      </div>
    );
  }

  const dvdCount = queue.filter((d) => d.type === "dvd").length;
  const cdCount = queue.filter((d) => d.type === "cd").length;
  const parts = [];
  if (dvdCount > 0) parts.push(`${dvdCount} DVD${dvdCount !== 1 ? "s" : ""}`);
  if (cdCount > 0) parts.push(`${cdCount} CD${cdCount !== 1 ? "s" : ""}`);
  const summary = `${queue.length} disc${queue.length !== 1 ? "s" : ""} awaiting identification` +
    (parts.length ? ` (${parts.join(", ")})` : "");

  return (
    <div>
      <div className="panel">
        <h2>Identification Queue</h2>
        {queue.length === 0 ? (
          <div className="empty-state">✓ No discs awaiting identification</div>
        ) : (
          <>
            <div className="queue-summary">{summary}</div>
            <div className="identification-queue">
              {queue.map((disc) => (
                <QueueEntry key={disc.id} disc={disc} onIdentify={setSelectedDisc} />
              ))}
            </div>
          </>
        )}
      </div>

      {selectedDisc && selectedDisc.type === "dvd" && (
        <DvdIdentifyPanel
          disc={selectedDisc}
          onConfirm={() => { setSelectedDisc(null); fetchQueue(); }}
          onSkip={() => setSelectedDisc(null)}
        />
      )}
      {selectedDisc && selectedDisc.type !== "dvd" && (
        <CdIdentifyPlaceholder disc={selectedDisc} onClose={() => setSelectedDisc(null)} />
      )}
    </div>
  );
}
