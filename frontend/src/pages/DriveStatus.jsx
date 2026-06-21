import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

function Countdown({ scheduledStart }) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  if (!scheduledStart) {
    return null;
  }

  const remainingSeconds = Math.ceil((new Date(scheduledStart).getTime() - now) / 1000);

  if (remainingSeconds <= 0) {
    return null;
  }

  return <p className="countdown">Starting in {remainingSeconds}s</p>;
}

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

const HEARTBEAT_STALE_THRESHOLD_MS = 10000;

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
  const waitingForId = discStatus === "ripped" && !disc?.temp_name;

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

function TempNameInput({ disc, onSaved }) {
  const [value, setValue] = useState(disc.temp_name || "");
  const [saving, setSaving] = useState(false);

  // Sync input if disc changes (e.g. after a refresh)
  useEffect(() => {
    setValue(disc.temp_name || "");
  }, [disc.temp_name]);

  const needsName = (disc.status === "ripped" || disc.status === "encoding") && !disc.temp_name;

  async function handleSave() {
    setSaving(true);
    try {
      await api.saveTempName(disc.id, value.trim() || null);
      onSaved();
    } finally {
      setSaving(false);
    }
  }

  function handleCopyLabel() {
    setValue(disc.disc_fingerprint);
  }

  return (
    <div className={`temp-name-row${needsName ? " needs-name" : ""}`}>
      <input
        type="text"
        placeholder="Working title…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSave()}
        className={needsName ? "input-warning" : ""}
      />
      {disc.disc_fingerprint && (
        <button
          type="button"
          className="copy-label-btn"
          onClick={handleCopyLabel}
          title="Copy disc label"
        >
          ↓
        </button>
      )}
      <button onClick={handleSave} disabled={saving}>
        {saving ? "Saving…" : "Save"}
      </button>
      {needsName && <span className="warning-hint">Add a name before ejecting</span>}
    </div>
  );
}

function DiscStatusZone({ disc, rippingEnabled }) {
  return (
    <div className="disc-status-zone">
      <div className="disc-status-row">
        <span className={`status-pill ${disc.status}`}>{disc.status}</span>
        <span className="disc-id-label">
          {disc.type ? disc.type.toUpperCase() : "Disc"} #{disc.id}
          {disc.disc_fingerprint ? ` · ${disc.disc_fingerprint}` : ""}
        </span>
      </div>

      {disc.status === "queued" && !rippingEnabled && !disc.scheduled_start && (
        <p className="countdown">Ripping paused - disc detected, waiting</p>
      )}

      {disc.status === "queued" && rippingEnabled && disc.scheduled_start && (
        <Countdown scheduledStart={disc.scheduled_start} />
      )}

      {(disc.status === "ripping" || disc.status === "building") && (
        <ProgressBar percent={disc.progress_percent} stage={disc.progress_stage} />
      )}
    </div>
  );
}

function DrivePanel({ drive, onRefresh, rippingEnabled }) {
  const disc = drive.current_disc;

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

      <div className="drive-row-status">
        {!drive.region_known ? (
          <span className="status-empty-note">Region unknown — read region before ripping</span>
        ) : !disc ? (
          <span className="status-pill idle">idle</span>
        ) : (
          <DiscStatusZone disc={disc} rippingEnabled={rippingEnabled} />
        )}
      </div>

      <div className="drive-row-temp-name">
        {disc && <TempNameInput disc={disc} onSaved={onRefresh} />}
      </div>

      <div className="drive-row-eject">
        <DirectEjectButton drive={drive} onRefresh={onRefresh} />
      </div>
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

  useEffect(() => {
    fetchDrives();
    fetchMaxRippers();
    fetchRippingEnabled();
    fetchServiceStatus();
    fetchServiceHeartbeat();
    const interval = setInterval(() => {
      fetchDrives();
      fetchRippingEnabled();
      fetchServiceStatus();
      fetchServiceHeartbeat();
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchDrives, fetchMaxRippers, fetchRippingEnabled, fetchServiceStatus, fetchServiceHeartbeat]);

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
        <ServiceStatusIndicator
          serviceStatus={serviceStatus}
          serviceHeartbeat={serviceHeartbeat}
          saving={stoppingService}
          onStop={handleStopService}
        />
      </div>

      <div className="drive-list">
        {drives.map((drive) => (
          <DrivePanel key={drive.id} drive={drive} onRefresh={fetchDrives} rippingEnabled={rippingEnabled} />
        ))}
      </div>
    </div>
  );
}
