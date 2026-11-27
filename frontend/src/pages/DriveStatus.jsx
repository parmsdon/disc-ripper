import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import DiscDeleteDialog from "../components/DiscDeleteDialog";

function ProgressBar({ percent, stage, makemkv }) {
  const pct = percent ?? 0;
  const labelText = stage ? `${stage} ${pct}%` : `${pct}%`;
  return (
    <div className="progress-bar-row">
      <div className="progress-bar-track">
        <div className={`progress-bar-fill${makemkv ? " makemkv" : ""}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="progress-bar-label" title={labelText}>{labelText}</span>
    </div>
  );
}

function MaxRippersControl({ maxRippers, driveCount, onChange }) {
  const [saving, setSaving] = useState(false);

  async function update(newValue) {
    const clamped = Math.max(1, newValue);
    if (clamped === maxRippers) return;
    setSaving(true);
    try {
      await api.setMaxRippers(clamped);
      onChange();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="control-bar-group">
      <span className="control-bar-label">Max simultaneous rips:</span>
      <button onClick={() => update(maxRippers - 1)} disabled={saving || maxRippers <= 1}>
        −
      </button>
      <input
        type="number"
        min="1"
        className="max-rippers-input"
        value={maxRippers}
        disabled={saving}
        onChange={(e) => {
          const parsed = parseInt(e.target.value, 10);
          if (!Number.isNaN(parsed)) update(parsed);
        }}
      />
      <button onClick={() => update(maxRippers + 1)} disabled={saving}>
        +
      </button>
      <span className="text-dim">(currently {maxRippers} of {driveCount} drives)</span>
    </div>
  );
}

function RippingToggle({ rippingEnabled, saving, onToggle }) {
  return (
    <button
      className={`ripping-toggle${rippingEnabled ? " active" : ""}`}
      onClick={onToggle}
      disabled={saving}
      title={rippingEnabled ? "Ripping is enabled - click to stop" : "Ripping is stopped - click to start"}
    >
      {rippingEnabled ? "Stop Ripping" : "Start Ripping"}
    </button>
  );
}

function formatRegionDisplay(region) {
  return region ? region.replace(/\s+/g, "") : region;
}

function RegionBadge({ drive, onRefresh }) {
  const [rereading, setRereading] = useState(false);

  async function handleStartRead() {
    await api.startRegionRead(drive.id);
    onRefresh();
  }

  async function handleReread() {
    setRereading(true);
    try {
      await api.rereadRegion(drive.id);
      onRefresh();
    } finally {
      setRereading(false);
    }
  }

  // The ripper service picks this up via pending_action on its next poll
  // (every 3s) - the page's own 5s refresh will show the result once done.
  if (drive.pending_action === "read_region") {
    return (
      <div className="region-cell">
        <span className={`status-pill ${drive.region_known ? "good" : "queued"}`}>
          Region: {drive.region_known ? formatRegionDisplay(drive.region) : "Unknown"}
        </span>
        <span className="pending-action-label">Reading…</span>
      </div>
    );
  }

  if (drive.region_known) {
    return (
      <div className="region-cell">
        <span className="status-pill good">Region: {formatRegionDisplay(drive.region)}</span>
        <button className="region-reread-link" onClick={handleReread} disabled={rereading}>
          {rereading ? "Clearing…" : "Re-read"}
        </button>
      </div>
    );
  }

  return (
    <div className="region-cell">
      <span className="status-pill queued">Region: Unknown</span>
      {drive.media_present ? (
        <button className="region-read-btn" onClick={handleStartRead}>Read region</button>
      ) : (
        <button className="region-read-btn" disabled title="Insert disc to read drive region">
          Read region
        </button>
      )}
    </div>
  );
}

function DirectEjectButton({ drive, onRefresh }) {
  const [ejecting, setEjecting] = useState(false);
  const disc = drive.current_disc;
  const discStatus = disc?.status;
  const closingTray = !!drive.tray_open;

  const idleTitle = closingTray ? "Close Tray" : "Eject";

  let disabledReason = null;
  if (discStatus === "ripping") {
    disabledReason = "Cannot eject while ripping";
  } else if (discStatus === "building") {
    disabledReason = "Cannot eject while building ISO";
  } else if (discStatus === "encoding") {
    disabledReason = "Cannot eject while encoding";
  } else if (drive.pending_action === "eject") {
    disabledReason = closingTray ? "Closing tray…" : "Eject in progress";
  } else if (drive.pending_action === "read_region") {
    disabledReason = "Reading region…";
  }

  async function handleClick() {
    setEjecting(true);
    try {
      await api.ejectDriveDirectly(drive.id);
      onRefresh();
    } finally {
      setEjecting(false);
    }
  }

  const title = disabledReason || (ejecting ? (closingTray ? "Closing tray…" : "Ejecting…") : idleTitle);

  return (
    <button
      className="eject-icon-btn"
      onClick={handleClick}
      disabled={!!disabledReason || ejecting}
      title={title}
    >
      ⏏
    </button>
  );
}

const _NAMEABLE_DISC_STATUSES = ["queued", "ripping", "building", "identifying", "ripped", "encoding", "done"];

// value/onChange are lifted to DrivePanel so the MB popover can pre-fill it.
// mbLookupStatus/onOpenMbPopover drive the small indicator button next to the input.
function TempNameInput({ disc, onSaved, value, onChange, mbLookupStatus, onOpenMbPopover }) {
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [conflict, setConflict] = useState(null);

  // Duplicate-name check for DVDs (fires when value differs from saved temp_name).
  useEffect(() => {
    if (disc.type !== "dvd") return;
    const trimmed = value.trim();
    if (!trimmed || trimmed === disc.temp_name) {
      setChecking(false);
      setConflict(null);
      return;
    }
    setChecking(true);
    setConflict(null);
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const result = await api.checkTempName(trimmed, disc.id, "dvd");
        if (!cancelled) {
          setConflict(result.available ? null : result);
          setChecking(false);
        }
      } catch {
        if (!cancelled) { setConflict(null); setChecking(false); }
      }
    }, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [value, disc.id, disc.type, disc.temp_name]);

  if (!_NAMEABLE_DISC_STATUSES.includes(disc.status)) {
    return null;
  }

  const savedName = disc.temp_name || "";
  const isUnchanged = value.trim() === savedName.trim();
  const hasValue = !!value.trim();
  const isCompleted = ["ripped", "encoding", "done"].includes(disc.status);
  const isLocked = isCompleted && disc.name_confirmed && isUnchanged;

  async function handleSave() {
    setSaving(true);
    try {
      await api.saveTempName(disc.id, value.trim() || null);
      onSaved();
    } finally {
      setSaving(false);
    }
  }

  function handleChange(newValue) {
    onChange(newValue);
    // Unconfirm on server when user edits away from the saved name.
    if (disc.name_confirmed && newValue.trim() !== savedName.trim()) {
      api.unconfirmName(disc.id).catch(() => {});
    }
  }

  // Button label and style
  let btnLabel, btnClass, btnDisabled;
  if (isLocked) {
    btnLabel = "Confirm";
    btnClass = "confirm-btn confirmed";
    btnDisabled = true;
  } else if (isUnchanged && disc.name_confirmed) {
    btnLabel = "Confirm";
    btnClass = "confirm-btn confirmed";
    btnDisabled = true;
  } else {
    btnLabel = saving ? "Saving…" : (isUnchanged ? "Confirm" : "Save");
    btnClass = "confirm-btn prominent";
    btnDisabled = saving || checking || !!conflict || !hasValue;
  }

  return (
    <div className="temp-name-row">
      <input
        type="text"
        placeholder="Working title…"
        value={value}
        readOnly={isLocked}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && !btnDisabled && handleSave()}
      />
      {disc.type === "cd" && mbLookupStatus === "pending" && (
        <button type="button" className="mb-status-btn" disabled title="Looking up in MusicBrainz…">
          ···
        </button>
      )}
      {disc.type === "cd" && mbLookupStatus === "found" && (
        <button type="button" className="mb-status-btn" onClick={onOpenMbPopover} title="MusicBrainz matches available">
          ♫
        </button>
      )}
      <button
        className={btnClass}
        onClick={handleSave}
        disabled={btnDisabled}
        title={conflict ? "Already in use by another DVD" : undefined}
      >
        {btnLabel}
      </button>
      {conflict && <span className="temp-name-conflict">{conflict.conflict_temp_name} (conflict)</span>}
    </div>
  );
}

const _IN_PROGRESS_DISC_STATUSES = ["queued", "ripping", "building"];
const _TERMINAL_DISC_STATUSES = ["ripped", "encoding", "done", "error", "protected"];

function CancelRipButton({ disc, onRefresh }) {
  const [cancelling, setCancelling] = useState(false);

  async function handleClick() {
    setCancelling(true);
    try {
      await api.cancelRip(disc.id);
      onRefresh();
    } catch (e) {
      console.error("Cancel rip failed:", e);
    } finally {
      setCancelling(false);
    }
  }

  return (
    <button
      className="cancel-rip-btn"
      onClick={handleClick}
      disabled={cancelling}
      title="Cancel this rip and eject the disc"
    >
      {cancelling ? "Cancelling…" : "Cancel"}
    </button>
  );
}

function RetryRipButton({ disc, onRefresh }) {
  const [retrying, setRetrying] = useState(false);

  async function handleClick() {
    setRetrying(true);
    try {
      await api.retryRip(disc.id);
      onRefresh();
    } catch (e) {
      console.error("Retry rip failed:", e);
    } finally {
      setRetrying(false);
    }
  }

  return (
    <button
      className="retry-rip-btn"
      onClick={handleClick}
      disabled={retrying}
      title="Retry the rip for this disc"
    >
      {retrying ? "Retrying…" : "Retry Rip"}
    </button>
  );
}

function DeleteDiscButton({ disc, onRefresh }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="cancel-rip-btn delete-disc-drive-btn"
        onClick={() => setOpen(true)}
        title="Delete this disc record and its files"
      >
        Delete
      </button>
      {open && (
        <DiscDeleteDialog
          discId={disc.id}
          onClose={() => setOpen(false)}
          onDeleted={() => { setOpen(false); onRefresh(); }}
        />
      )}
    </>
  );
}

function DiscStatusZone({ disc }) {
  const inProgress = _IN_PROGRESS_DISC_STATUSES.includes(disc.status);

  // Status pill: spec states Reading → Ripping → Clean | Dirty | Failed
  let statusClass, statusLabel;
  if (disc.status === "queued") {
    statusClass = "queued";
    statusLabel = "Reading";
  } else if (disc.status === "ripping" || disc.status === "building") {
    statusClass = "ripping";
    statusLabel = "Ripping";
  } else if (disc.status === "cancelled") {
    statusClass = "clean";
    statusLabel = "Not ripped";
  } else if (disc.status === "error" || disc.status === "protected") {
    statusClass = "failed";
    statusLabel = "Failed";
  } else if (disc.rip_quality === "dirty") {
    statusClass = "dirty";
    statusLabel = "Dirty";
  } else {
    statusClass = "clean";
    statusLabel = "Clean";
  }

  // Method pill — not shown while queued (method not yet determined)
  let methodClass, methodLabel, methodTooltip;
  const makemkvActive = disc.rip_method === "makemkv" || !!disc.rip_reason;
  if (makemkvActive) {
    if (disc.rip_reason === "protected") {
      methodClass = "makemkv-protected";
      methodLabel = "MakeMKV · protected";
      methodTooltip = "Disc is copy-protected; ripped with MakeMKV";
    } else {
      methodClass = "makemkv-fallback";
      methodLabel = "MakeMKV · fallback";
      methodTooltip =
        disc.rip_reason === "dvdbackup_failed" ? "dvdbackup failed; re-ripped with MakeMKV" :
        disc.rip_reason === "dirty" ? "dvdbackup reported read errors; re-ripped with MakeMKV" :
        "Re-ripped with MakeMKV";
    }
  } else {
    methodClass = "dvdbackup";
    methodLabel = "dvdbackup";
    methodTooltip = "Ripped with dvdbackup";
  }

  return (
    <div className="disc-status-zone">
      <div className="disc-status-row">
        <span className={`status-pill ${statusClass}`}>{statusLabel}</span>
        {disc.type && (
          <span className={`queue-entry-type-badge media-${statusClass}`}>{disc.type.toUpperCase()}</span>
        )}
        <span
          className="disc-id-label"
          title={[disc.volume_label, disc.disc_fingerprint].filter(Boolean).join(" · ") || undefined}
        >
          #{disc.id}{disc.temp_name ? ` · ${disc.temp_name}` : disc.volume_label ? ` · ${disc.volume_label}` : ""}
        </span>
        {disc.status !== "queued" && (
          <span className={`method-pill ${methodClass}`} title={methodTooltip}>{methodLabel}</span>
        )}
      </div>

      {(disc.status === "ripping" || disc.status === "building") && (
        <ProgressBar percent={disc.progress_percent} stage={disc.progress_stage} makemkv={makemkvActive} />
      )}
    </div>
  );
}


function MbPopover({ candidates, onSelect, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const hasUnknownDisc = candidates.some(
    (c) => c.medium_count > 1 && c.medium_position == null
  );

  return (
    <div className="mb-popover-backdrop" onClick={onClose}>
      <div className="mb-popover" onClick={(e) => e.stopPropagation()}>
        <div className="mb-popover-header">
          <span>CD matches</span>
          <button className="mb-popover-close" onClick={onClose}>×</button>
        </div>
        <div className="mb-popover-list">
          {candidates.length === 0 ? (
            <div className="empty-state">No matches found</div>
          ) : (
            candidates.map((c) => {
              const unknownDisc = c.medium_count > 1 && c.medium_position == null;
              return (
                <button
                  key={c.id}
                  type="button"
                  className={`mb-popover-item${unknownDisc ? " mb-popover-item--unknown-disc" : ""}`}
                  onClick={() => {
                    let title = c.title;
                    if (c.medium_count > 1) {
                      const suffix = c.medium_title || `Disc ${c.medium_position ?? "?"} of ${c.medium_count}`;
                      title = `${title} (${suffix})`;
                    }
                    onSelect(title);
                    onClose();
                  }}
                >
                  <span className="mb-popover-title">
                    {c.title}
                    {c.medium_count > 1 && (
                      <span className="mb-popover-disc">
                        {" "}({c.medium_title || `Disc ${c.medium_position ?? "?"} of ${c.medium_count}`})
                      </span>
                    )}
                    {c.source === "discogs" && (
                      <span className="candidate-source-badge candidate-source-badge--discogs">Discogs</span>
                    )}
                    {c.source === "musicbrainz" && (
                      <span className="candidate-source-badge candidate-source-badge--mb">MB</span>
                    )}
                  </span>
                  <span className="mb-popover-meta">
                    {[c.artist, c.year, c.track_count != null ? `${c.track_count} tracks` : null]
                      .filter(Boolean).join(" · ")}
                  </span>
                </button>
              );
            })
          )}
        </div>
        {hasUnknownDisc && (
          <div className="mb-popover-unknown-disc-warning">
            ⚠ Disc number unknown — please edit the ? in the working title before saving
          </div>
        )}
      </div>
    </div>
  );
}

function DrivePanel({ drive, onRefresh }) {
  // An open tray can't have a disc actually loaded, no matter what
  // current_disc the backend still has on record for it (e.g. ejection
  // is in flight, or the disc was removed before the next API refresh) -
  // treat the drive as disc-less for display purposes until it's closed
  // again, rather than showing stale per-disc state.
  const disc = drive.tray_open ? null : drive.current_disc;

  const [tempNameValue, setTempNameValue] = useState("");
  const [mbPopoverOpen, setMbPopoverOpen] = useState(false);
  const [candidates, setCandidates] = useState(null);

  useEffect(() => {
    setTempNameValue(disc?.temp_name || disc?.volume_label || "");
    setMbPopoverOpen(false);
    setCandidates(null);
  }, [disc?.id]);

  async function handleOpenMbPopover() {
    const results = await api.getDiscCandidates(disc.id).catch(() => []);
    setCandidates(results);
    setMbPopoverOpen(true);
  }

  return (
    <div className="drive-row">
      <div className="drive-row-identity">
        <span className="drive-icon">💽</span>
        <div className="drive-identity-text">
          <div className="drive-label">{drive.label || drive.device_path}</div>
          <div className="device-path">{drive.device_path}</div>
        </div>
      </div>

      <div className="drive-row-action">
        {disc && (disc.status === "ripping" || disc.status === "building") && (
          <CancelRipButton disc={disc} onRefresh={onRefresh} />
        )}
      </div>

      <div className="drive-row-status">
        {drive.tray_open ? (
          <span className="status-pill open">Open</span>
        ) : !drive.region_known ? (
          <span className="status-empty-note">Region unknown — read region before ripping</span>
        ) : !disc ? (
          <span className="status-pill idle">idle</span>
        ) : (
          <DiscStatusZone disc={disc} />
        )}
      </div>

      <div className="drive-row-temp-name">
        {disc && (
          <TempNameInput
            disc={disc}
            onSaved={onRefresh}
            value={tempNameValue}
            onChange={setTempNameValue}
            mbLookupStatus={disc.mb_lookup_status}
            onOpenMbPopover={handleOpenMbPopover}
          />
        )}
      </div>

      <div className="drive-row-eject">
        <DirectEjectButton drive={drive} onRefresh={onRefresh} />
      </div>

      {mbPopoverOpen && candidates && (
        <MbPopover
          candidates={candidates}
          onSelect={setTempNameValue}
          onClose={() => setMbPopoverOpen(false)}
        />
      )}
    </div>
  );
}

export default function DriveStatus() {
  const [drives, setDrives] = useState(null);
  const [maxRippers, setMaxRippers] = useState(null);
  const [rippingEnabled, setRippingEnabled] = useState(false);
  const [savingRippingEnabled, setSavingRippingEnabled] = useState(false);
  const [error, setError] = useState(null);
  const fetchDrives = useCallback(() => {
    api.drives()
      .then(setDrives)
      .catch((e) => setError(e.message));
  }, []);

  const fetchMaxRippers = useCallback(() => {
    api.getMaxRippers()
      .then((data) => setMaxRippers(data.max_rippers))
      .catch((e) => setError(e.message));
  }, []);

  const fetchRippingEnabled = useCallback(() => {
    api.getRippingEnabled()
      .then((data) => setRippingEnabled(data.ripping_enabled))
      .catch((e) => setError(e.message));
  }, []);

  async function toggleRippingEnabled() {
    setSavingRippingEnabled(true);
    try {
      const data = await api.setRippingEnabled(!rippingEnabled);
      setRippingEnabled(data.ripping_enabled);
    } finally {
      setSavingRippingEnabled(false);
    }
  }

  useEffect(() => {
    fetchDrives();
    fetchMaxRippers();
    fetchRippingEnabled();
    const interval = setInterval(() => {
      fetchDrives();
      fetchRippingEnabled();
    }, 1000);
    return () => clearInterval(interval);
  }, [fetchDrives, fetchMaxRippers, fetchRippingEnabled]);

  if (error) {
    return <div className="panel"><h2>Drive Status</h2><div className="empty-state">Error loading drives: {error}</div></div>;
  }

  if (drives === null) {
    return <div className="panel"><h2>Drive Status</h2><div className="empty-state">Loading…</div></div>;
  }

  if (drives.length === 0) {
    return (
      <div className="panel">
        <h2>Drive Status</h2>
        <div className="empty-state">
          No drives configured for this environment. Add drives to config/&lt;env&gt;.yaml
          and seed the drives table.
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="control-bar">
        <MaxRippersControl
          maxRippers={maxRippers ?? 1}
          driveCount={drives.length}
          onChange={fetchMaxRippers}
        />
        <RippingToggle
          rippingEnabled={rippingEnabled}
          saving={savingRippingEnabled}
          onToggle={toggleRippingEnabled}
        />
      </div>

      <div className="drive-list">
        {drives.map((drive) => (
          <DrivePanel
            key={drive.id}
            drive={drive}
            onRefresh={fetchDrives}
          />
        ))}
      </div>
    </div>
  );
}
