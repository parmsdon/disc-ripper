import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

function ProgressBar({ percent, stage }) {
  const pct = percent ?? 0;
  return (
    <div className="progress-bar-row">
      <div className="progress-bar-track">
        <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="progress-bar-label">{stage ? `${stage}: ${pct}%` : `${pct}%`}</span>
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

function RippingToggle({ rippingEnabled, saving, onToggle, disabled }) {
  return (
    <button
      className={`ripping-toggle${rippingEnabled ? " active" : ""}`}
      onClick={onToggle}
      disabled={saving || disabled}
      title={disabled ? "Stop reconcile mode before starting ripping" : rippingEnabled ? "Ripping is enabled - click to stop" : "Ripping is stopped - click to start"}
    >
      {rippingEnabled ? "Stop Ripping" : "Start Ripping"}
    </button>
  );
}

const HEARTBEAT_STALE_THRESHOLD_MS = 60000;

function ServiceStatusIndicator({ serviceStatus, serviceHeartbeat, saving, onStop }) {
  const [now, setNow] = useState(Date.now());
  const [stopRequested, setStopRequested] = useState(false);

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  const heartbeatMs = serviceHeartbeat ? new Date(serviceHeartbeat).getTime() : null;
  const isStale = heartbeatMs === null || now - heartbeatMs > HEARTBEAT_STALE_THRESHOLD_MS;

  // The request has been resolved one way or another once the service is
  // confirmed stopped, or it died mid-shutdown without completing cleanly
  // (still "running" but no longer responding) - clear the local flag so
  // the button's disabled state goes back to being driven purely by
  // serviceStatus, in case the service is restarted later and needs to
  // be stoppable again.
  useEffect(() => {
    if (serviceStatus === "stopped" || (serviceStatus === "running" && isStale)) {
      setStopRequested(false);
    }
  }, [serviceStatus, isStale]);

  let pillClass = "idle";
  let pillText = "Ripper service: stopped";
  let detailText = null;

  if (serviceStatus === "running" && !isStale) {
    pillClass = "good";
    pillText = "Ripper service: running";
  } else if (serviceStatus === "running" && isStale) {
    pillClass = "error";
    pillText = "Ripper service: not responding";
  } else if (serviceStatus === "stopped" && heartbeatMs !== null) {
    detailText = `stopped at ${new Date(heartbeatMs).toLocaleTimeString()}`;
  }

  function handleStopClick() {
    setStopRequested(true);
    onStop();
  }

  const buttonLabel = stopRequested && serviceStatus === "running" ? "Stopping…" : "Stop Service";

  return (
    <div className="control-bar-group">
      <span className={`status-pill ${pillClass}`}>{pillText}</span>
      {detailText && <span className="text-dim">{detailText}</span>}
      <button
        onClick={handleStopClick}
        disabled={saving || stopRequested || serviceStatus === "stopped"}
        title="Request a clean shutdown of the ripper service"
      >
        {buttonLabel}
      </button>
    </div>
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
  const waitingForId = discStatus === "identifying";

  let idleTitle;
  if (closingTray) {
    idleTitle = "Close Tray";
  } else if (waitingForId) {
    idleTitle = "Eject to view disc";
  } else {
    idleTitle = "Eject";
  }

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

const _NAMEABLE_DISC_STATUSES = ["queued", "ripping", "building", "identifying"];

// value/onChange are lifted to DrivePanel so the MB popover can pre-fill it.
// mbLookupStatus/mbHasResults/onOpenMbPopover drive the small indicator button
// that lives in the same button row as Copy Label and Copy Current.
function TempNameInput({ disc, onSaved, value, onChange, mbLookupStatus, mbHasResults, onOpenMbPopover }) {
  const [saving, setSaving] = useState(false);

  // Naming is offered for the whole active lifetime of a disc (queued
  // through identifying), so the user can type a title in as soon as it's
  // detected rather than waiting for the rip to finish. Once status
  // reaches "ripped" - either set directly because a name was already
  // saved when building completed, or via the identifying -> ripped
  // auto-transition - this input disappears for good. There's
  // deliberately no edit-after-lock path: the working title is just a
  // bridge until real catalog matching exists.
  if (!_NAMEABLE_DISC_STATUSES.includes(disc.status)) {
    return null;
  }

  async function handleSave() {
    setSaving(true);
    try {
      await api.saveTempName(disc.id, value.trim() || null);
      onChange("");
      onSaved();
    } finally {
      setSaving(false);
    }
  }

  function handleCopyLabel() {
    onChange(disc.disc_fingerprint);
  }

  function handleCopyCurrent() {
    onChange(disc.temp_name);
  }

  return (
    <div className="temp-name-row">
      <input
        type="text"
        placeholder="Working title…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSave()}
        className="input-warning"
      />
      {/* CD disc_fingerprint is a CDDB-style hash, not a human-readable
          label - only worth copying for DVDs (volume id/volume set id). */}
      {disc.type !== "cd" && disc.disc_fingerprint && (
        <button
          type="button"
          className="copy-label-btn"
          onClick={handleCopyLabel}
          title="Copy disc label"
        >
          ↓
        </button>
      )}
      {disc.temp_name && (
        <button
          type="button"
          className="copy-current-btn"
          onClick={handleCopyCurrent}
          title="Copy current working title"
        >
          ↻
        </button>
      )}
      {disc.type === "cd" && mbLookupStatus === "pending" && (
        <button type="button" className="mb-status-btn" disabled title="Looking up in MusicBrainz…">
          ···
        </button>
      )}
      {disc.type === "cd" && mbLookupStatus === "found" && mbHasResults && (
        <button type="button" className="mb-status-btn" onClick={onOpenMbPopover} title="MusicBrainz matches available">
          ♫
        </button>
      )}
      <button
        onClick={handleSave}
        disabled={saving || !value.trim() || value.trim() === disc.temp_name}
      >
        {saving ? "Saving…" : "Save"}
      </button>
      {disc.status === "identifying" && (
        <span className="warning-hint">This can't be changed once saved</span>
      )}
    </div>
  );
}

const _IN_PROGRESS_DISC_STATUSES = ["queued", "ripping", "building"];

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

function DiscStatusZone({ disc }) {
  // Not scoped to a particular status - dirty is now flagged live as
  // soon as a read error streams in (see rip_worker._flag_dirty_rip_live),
  // so this can be true while the disc is still "ripping"/"building".
  const isDirty = disc.rip_quality === "dirty";
  const isRerip = disc.rip_attempt_count > 1;
  const reripInProgress = isRerip && _IN_PROGRESS_DISC_STATUSES.includes(disc.status);
  const pillLabel = reripInProgress ? "re-ripping" : disc.status;

  return (
    <div className="disc-status-zone">
      <div className="disc-status-row">
        <span className={`status-pill ${disc.status}`}>{pillLabel}</span>
        {disc.type ? (
          <span className={`queue-entry-type-badge ${disc.type}`}>{disc.type.toUpperCase()}</span>
        ) : null}
        <span className="disc-id-label">
          #{disc.id}
          {disc.disc_fingerprint ? ` · ${disc.disc_fingerprint}` : ""}
        </span>
        {disc.temp_name && (
          <span className="working-title-badge" title="Working title">
            Working title: {disc.temp_name}
          </span>
        )}
        {isDirty && (
          <span
            className="dirty-rip-badge"
            title="Rip completed with read errors — use Retry Rip to attempt again"
          >
            ⚠ Dirty rip
          </span>
        )}
        {isRerip && <span className="attempt-badge">Attempt {disc.rip_attempt_count}</span>}
      </div>

      {disc.status === "error" && disc.error_message && (
        <p className="error-hint">{disc.error_message}</p>
      )}

      {reripInProgress && (
        <p className="rerip-hint">Re-ripping after a previous dirty/failed attempt</p>
      )}

      {disc.status === "identifying" && (
        <p className="identifying-hint">Disc ripped — please identify before this drive can be reused</p>
      )}

      {(disc.status === "ripping" || disc.status === "building") && (
        <ProgressBar percent={disc.progress_percent} stage={disc.progress_stage} />
      )}
    </div>
  );
}

function useMbCandidates(disc) {
  const [candidates, setCandidates] = useState(null);

  useEffect(() => { setCandidates(null); }, [disc?.id]);

  useEffect(() => {
    if (disc?.mb_lookup_status === "found" && candidates === null) {
      api.getDiscCandidates(disc.id)
        .then(setCandidates)
        .catch(() => setCandidates([]));
    }
  }, [disc?.mb_lookup_status, disc?.id, candidates]);

  return candidates;
}

function MbPopover({ candidates, onSelect, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="mb-popover-backdrop" onClick={onClose}>
      <div className="mb-popover" onClick={(e) => e.stopPropagation()}>
        <div className="mb-popover-header">
          <span>MusicBrainz matches</span>
          <button className="mb-popover-close" onClick={onClose}>×</button>
        </div>
        <div className="mb-popover-list">
          {candidates.length === 0 ? (
            <div className="empty-state">No matches found</div>
          ) : (
            candidates.map((c) => (
              <button
                key={c.id}
                type="button"
                className="mb-popover-item"
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
                </span>
                <span className="mb-popover-meta">
                  {[c.artist, c.year, c.track_count != null ? `${c.track_count} tracks` : null]
                    .filter(Boolean).join(" · ")}
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function MatchIsoPanel({ drive, oldIsos, onClose, onSuccess }) {
  const disc = drive.current_disc;
  const [titleValue, setTitleValue] = useState(disc?.temp_name || "");
  const [selectedIso, setSelectedIso] = useState(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const filteredIsos = oldIsos.filter(
    (iso) => iso.filename.toLowerCase().includes(search.toLowerCase())
  );

  async function handleConfirm() {
    if (!selectedIso || !disc) return;
    setLoading(true);
    setError(null);
    try {
      await api.reconcileDisc({
        drive_id: drive.id,
        disc_fingerprint: disc.disc_fingerprint,
        old_iso_filename: selectedIso.filename,
        temp_name: titleValue.trim() || disc.disc_fingerprint,
      });
      onSuccess();
      onClose();
    } catch (e) {
      setError(e.message);
      setLoading(false);
    }
  }

  return (
    <div className="match-iso-panel-backdrop" onClick={onClose}>
      <div className="match-iso-panel" onClick={(e) => e.stopPropagation()}>
        <div className="match-iso-panel-header">
          <div>
            <div className="match-iso-panel-title">Match ISO to Disc</div>
            <div className="match-iso-panel-fp">{disc?.disc_fingerprint || "Unknown disc"}</div>
          </div>
          <button className="mb-popover-close" onClick={onClose}>×</button>
        </div>
        <div className="match-iso-panel-body">
          <div className="match-iso-title-row">
            <span className="match-iso-title-label">Working title</span>
            <input
              type="text"
              className="match-iso-title-input"
              value={titleValue}
              onChange={(e) => setTitleValue(e.target.value)}
              placeholder="Working title…"
            />
          </div>
          <input
            type="text"
            className="match-iso-search"
            placeholder="Search ISOs…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="old-iso-list">
            {filteredIsos.length === 0 ? (
              <div className="empty-state">No ISOs found</div>
            ) : filteredIsos.map((iso) => (
              <div
                key={iso.filename}
                className={`old-iso-row${selectedIso?.filename === iso.filename ? " selected" : ""}${!iso.is_valid ? " old-iso-invalid" : ""}`}
                onClick={() => setSelectedIso(iso)}
              >
                <span className="old-iso-filename">{iso.filename}</span>
                <span className="old-iso-size">{iso.size_display}</span>
                {!iso.is_valid && <span className="old-iso-warn">⚠ small</span>}
              </div>
            ))}
          </div>
          {error && <div className="match-iso-error">{error}</div>}
        </div>
        <div className="match-iso-panel-footer">
          <button onClick={onClose}>Skip</button>
          <button onClick={handleConfirm} disabled={!selectedIso || loading}>
            {loading ? "Matching…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}


function DrivePanel({ drive, onRefresh, reconcileMode, onMatchIso }) {
  // An open tray can't have a disc actually loaded, no matter what
  // current_disc the backend still has on record for it (e.g. ejection
  // is in flight, or the disc was removed before the next API refresh) -
  // treat the drive as disc-less for display purposes until it's closed
  // again, rather than showing stale per-disc state.
  const disc = drive.tray_open ? null : drive.current_disc;

  const [tempNameValue, setTempNameValue] = useState("");
  const [mbPopoverOpen, setMbPopoverOpen] = useState(false);
  const candidates = useMbCandidates(disc);

  useEffect(() => {
    setTempNameValue("");
    setMbPopoverOpen(false);
  }, [disc?.id]);

  return (
    <div className="drive-row">
      <div className="drive-row-identity">
        <span className="drive-icon">💽</span>
        <div className="drive-identity-text">
          <div className="drive-label">{drive.label || drive.device_path}</div>
          <div className="device-path">{drive.device_path}</div>
        </div>
      </div>

      <div className="drive-row-region">
        <RegionBadge drive={drive} onRefresh={onRefresh} />
      </div>

      <div className="drive-row-action">
        {disc && (disc.status === "ripping" || disc.status === "building") && (
          <CancelRipButton disc={disc} onRefresh={onRefresh} />
        )}
        {disc && (
          disc.status === "error" || (
            disc.rip_quality === "dirty"
            && disc.status !== "ripping"
            && disc.status !== "building"
            && disc.status !== "queued"
          )
        ) && (
          <RetryRipButton disc={disc} onRefresh={onRefresh} />
        )}
        {reconcileMode && disc && disc.type === "dvd" && disc.disc_fingerprint
          && disc.temp_name && !["ripped", "identifying", "done"].includes(disc.status) && (
          <button
            className="match-iso-btn"
            onClick={() => onMatchIso(drive)}
            title="Match an existing ISO to this disc"
          >
            Match ISO
          </button>
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
            mbHasResults={Boolean(candidates && candidates.length > 0)}
            onOpenMbPopover={() => setMbPopoverOpen(true)}
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
  const [serviceStatus, setServiceStatus] = useState("stopped");
  const [serviceHeartbeat, setServiceHeartbeat] = useState(null);
  const [stoppingService, setStoppingService] = useState(false);
  const [error, setError] = useState(null);
  const [reconcileMode, setReconcileMode] = useState(false);
  const [oldIsos, setOldIsos] = useState([]);
  const [matchIsoDrive, setMatchIsoDrive] = useState(null);

  const fetchDrives = useCallback(() => {
    api.drives()
      .then(setDrives)
      .catch((e) => setError(e.message));
  }, []);

  const fetchOldIsos = useCallback(() => {
    api.getOldIsos()
      .then(setOldIsos)
      .catch(() => {});
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

  const fetchServiceStatus = useCallback(() => {
    api.getServiceStatus()
      .then((data) => setServiceStatus(data.service_status))
      .catch((e) => setError(e.message));
  }, []);

  const fetchServiceHeartbeat = useCallback(() => {
    api.getServiceHeartbeat()
      .then((data) => setServiceHeartbeat(data.service_heartbeat))
      .catch((e) => setError(e.message));
  }, []);

  async function handleStopService() {
    setStoppingService(true);
    try {
      await api.setServiceCommand("exit");
    } finally {
      setStoppingService(false);
    }
  }

  async function handleReconcileSuccess() {
    fetchDrives();
    try {
      const data = await api.getOldIsos();
      setOldIsos(data);
      if (data.length === 0) setReconcileMode(false);
    } catch (_) {}
  }

  useEffect(() => {
    fetchDrives();
    fetchMaxRippers();
    fetchRippingEnabled();
    fetchServiceStatus();
    fetchServiceHeartbeat();
    fetchOldIsos();
    const interval = setInterval(() => {
      fetchDrives();
      fetchRippingEnabled();
      fetchServiceStatus();
      fetchServiceHeartbeat();
    }, 1000);
    const isoInterval = setInterval(fetchOldIsos, 30000);
    return () => {
      clearInterval(interval);
      clearInterval(isoInterval);
    };
  }, [fetchDrives, fetchMaxRippers, fetchRippingEnabled, fetchServiceStatus, fetchServiceHeartbeat, fetchOldIsos]);

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
          disabled={reconcileMode}
        />
        {(oldIsos.length > 0 || reconcileMode) && (
          <div className="control-bar-group">
            <button
              className={`reconcile-btn${reconcileMode ? " active" : ""}`}
              onClick={() => setReconcileMode((m) => !m)}
              disabled={rippingEnabled}
              title={rippingEnabled ? "Stop ripping before reconciling" : "Match existing ISOs to detected discs"}
            >
              Reconcile ISOs{oldIsos.length > 0 ? ` (${oldIsos.length})` : ""}
            </button>
          </div>
        )}
        <ServiceStatusIndicator
          serviceStatus={serviceStatus}
          serviceHeartbeat={serviceHeartbeat}
          saving={stoppingService}
          onStop={handleStopService}
        />
      </div>

      <div className="drive-list">
        {drives.map((drive) => (
          <DrivePanel
            key={drive.id}
            drive={drive}
            onRefresh={fetchDrives}
            reconcileMode={reconcileMode}
            onMatchIso={setMatchIsoDrive}
          />
        ))}
      </div>

      {matchIsoDrive && (
        <MatchIsoPanel
          drive={matchIsoDrive}
          oldIsos={oldIsos}
          onClose={() => setMatchIsoDrive(null)}
          onSuccess={handleReconcileSuccess}
        />
      )}
    </div>
  );
}
