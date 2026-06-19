import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

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

      {!disc ? (
        <p><span className="status-pill idle">idle</span></p>
      ) : (
        <>
          <p>
            <span className={`status-pill ${disc.status}`}>{disc.status}</span>
          </p>

          {disc.status === "queued" && (
            <p className="countdown">Starting in 10s</p>
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
  const [error, setError] = useState(null);

  const fetchDrives = useCallback(() => {
    api.drives()
      .then(setDrives)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    fetchDrives();
    const interval = setInterval(fetchDrives, 5000);
    return () => clearInterval(interval);
  }, [fetchDrives]);

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
      {drives.map((drive) => (
        <DrivePanel key={drive.id} drive={drive} onRefresh={fetchDrives} />
      ))}
    </div>
  );
}
