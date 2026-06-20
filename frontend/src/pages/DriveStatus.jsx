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
    <div className="panel max-rippers-control">
      <label>
        Max simultaneous rips:{" "}
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
      </label>
      <span className="text-dim"> (currently {maxRippers} of {driveCount} drives)</span>
    </div>
  );
}

function RegionBadge({ drive, onRefresh }) {
  const [reading, setReading] = useState(false);
  const [rereading, setRereading] = useState(false);

  async function handleStartRead() {
    setReading(true);
    try {
      await api.startRegionRead(drive.id);
      onRefresh();
    } finally {
      setReading(false);
    }
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

  if (drive.region_known) {
    return (
      <div className="region-row">
        <span className="status-pill good">Region: {drive.region}</span>
        <button className="region-reread-link" onClick={handleReread} disabled={rereading}>
          {rereading ? "Clearing…" : "Re-read region"}
        </button>
      </div>
    );
  }

  return (
    <div className="region-row">
      <span className="status-pill queued">Region: Unknown</span>
      {/* Always enabled for now — no disc-presence detection until the ripper
          service exists. Once it does, disable with "Insert a disc first". */}
      <button onClick={handleStartRead} disabled={reading}>
        {reading ? "Starting…" : "Read Region"}
      </button>
    </div>
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

function DrivePanel({ drive, onRefresh }) {
  const disc = drive.current_disc;
  const [ejecting, setEjecting] = useState(false);

  async function handleEject() {
    setEjecting(true);
    try {
      await api.ejectDisc(disc.id);
      onRefresh();
    } finally {
      setEjecting(false);
    }
  }

  return (
    <div className="panel">
      <div className="drive-header">
        <h2>{drive.label || drive.device_path}</h2>
        <span className="device-path">{drive.device_path}</span>
      </div>

      <RegionBadge drive={drive} onRefresh={onRefresh} />

      {!drive.region_known ? (
        <p className="empty-state">Region unknown — read the region before ripping.</p>
      ) : !disc ? (
        <p><span className="status-pill idle">idle</span></p>
      ) : (
        <>
          <p>
            <span className={`status-pill ${disc.status}`}>{disc.status}</span>
          </p>

          {disc.status === "queued" && (
            <Countdown scheduledStart={disc.scheduled_start} />
          )}

          {disc.status === "ripping" && (
            <p className="progress-placeholder">Ripping in progress…</p>
          )}

          <TempNameInput disc={disc} onSaved={onRefresh} />

          {disc.status === "ripped" && (
            <button
              className="eject-btn"
              onClick={handleEject}
              disabled={ejecting}
            >
              {ejecting ? "Ejecting…" : "Eject"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

export default function DriveStatus() {
  const [drives, setDrives] = useState(null);
  const [maxRippers, setMaxRippers] = useState(null);
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

  useEffect(() => {
    fetchDrives();
    fetchMaxRippers();
    const interval = setInterval(fetchDrives, 5000);
    return () => clearInterval(interval);
  }, [fetchDrives, fetchMaxRippers]);

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
      <MaxRippersControl
        maxRippers={maxRippers ?? 1}
        driveCount={drives.length}
        onChange={fetchMaxRippers}
      />
      {drives.map((drive) => (
        <DrivePanel key={drive.id} drive={drive} onRefresh={fetchDrives} />
      ))}
    </div>
  );
}
