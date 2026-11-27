import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import CdIdentifyPanel from "./CdIdentifyPanel.jsx";
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

function QueueEntry({ disc, onIdentify, onRefresh }) {
  const rippedAt = formatRippedAt(disc.ripped_at);
  const [nameValue, setNameValue] = useState(disc.temp_name || disc.volume_label || "");
  const [nameSaving, setNameSaving] = useState(false);

  // Sync when this entry re-renders after a save.
  useEffect(() => {
    setNameValue(disc.temp_name || disc.volume_label || "");
  }, [disc.id, disc.temp_name]);

  const needsName = !disc.name_confirmed;
  const needsCatalog = (disc.type === "dvd" && !disc.catalog_id) ||
                       (disc.type === "cd"  && !disc.album_title);
  const isCompleted = ["ripped", "encoding", "done"].includes(disc.status);
  const savedName = disc.temp_name || "";
  const isUnchanged = nameValue.trim() === savedName.trim();
  const hasNameValue = !!nameValue.trim();
  const isLocked = isCompleted && disc.name_confirmed && isUnchanged;

  async function handleConfirmName() {
    if (!nameValue.trim()) return;
    setNameSaving(true);
    try {
      await api.saveTempName(disc.id, nameValue.trim());
      onRefresh();
    } catch (e) {
      console.error("Save name failed:", e);
    } finally {
      setNameSaving(false);
    }
  }

  function handleNameChange(newValue) {
    setNameValue(newValue);
    if (disc.name_confirmed && newValue.trim() !== savedName.trim()) {
      api.unconfirmName(disc.id).catch(() => {});
    }
  }

  let nameBtnLabel, nameBtnClass;
  if (nameSaving) { nameBtnLabel = "Saving…"; nameBtnClass = "confirm-btn prominent"; }
  else if (isUnchanged && disc.name_confirmed) { nameBtnLabel = "Confirm"; nameBtnClass = "confirm-btn confirmed"; }
  else if (isUnchanged) { nameBtnLabel = "Confirm"; nameBtnClass = "confirm-btn prominent"; }
  else { nameBtnLabel = "Save"; nameBtnClass = "confirm-btn prominent"; }

  const nameBtnDisabled = nameSaving || !hasNameValue || (isUnchanged && disc.name_confirmed);

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
        {disc.volume_label && !disc.temp_name && (
          <div className="queue-entry-meta">{disc.volume_label}</div>
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
        {needsName && (
          <span className="name-not-confirmed-badge">Name not confirmed</span>
        )}
      </div>
      {(needsName && !isLocked) && (
        <div className="queue-entry-name-row">
          <input
            type="text"
            className="queue-entry-name-input"
            placeholder="Working title…"
            value={nameValue}
            readOnly={isLocked}
            onChange={(e) => handleNameChange(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !nameBtnDisabled && handleConfirmName()}
          />
          <button
            className={nameBtnClass}
            onClick={handleConfirmName}
            disabled={nameBtnDisabled}
          >
            {nameBtnLabel}
          </button>
        </div>
      )}
      {disc.name_confirmed && needsCatalog && (
        <button onClick={() => onIdentify(disc)}>Identify</button>
      )}
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
        <h2>Identification</h2>
        <div className="empty-state">Error loading queue: {error}</div>
      </div>
    );
  }

  if (queue === null) {
    return (
      <div className="panel">
        <h2>Identification</h2>
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
                <QueueEntry key={disc.id} disc={disc} onIdentify={setSelectedDisc} onRefresh={fetchQueue} />
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
      {selectedDisc && selectedDisc.type === "cd" && (
        <CdIdentifyPanel
          disc={selectedDisc}
          onConfirm={() => { setSelectedDisc(null); fetchQueue(); }}
          onSkip={() => setSelectedDisc(null)}
        />
      )}
    </div>
  );
}
