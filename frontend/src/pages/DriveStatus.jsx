import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

function Countdown({ scheduledStart }) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  if (!scheduledStart) {
    return <p className="countdown">Starting…</p>;
  }

  const remainingSeconds = Math.ceil((new Date(scheduledStart).getTime() - now) / 1000);

  if (remainingSeconds <= 0) {
    return <p className="countdown">Starting…</p>;
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
          Region: {drive.region_known ? drive.region : "Unknown"}
        </span>
        <span className="pending-action-label">Reading…</span>
      </div>
    );
  }

  if (drive.region_known) {
    return (
      <div className="region-cell">
        <span className="status-pill good">Region: {drive.region}</span>
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

      {disc.status === "ripping" && (
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
    }, 5000);
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
          <DrivePanel key={drive.id} drive={drive} onRefresh={fetchDrives} rippingEnabled={rippingEnabled} />
        ))}
      </div>
    </div>
  );
}
