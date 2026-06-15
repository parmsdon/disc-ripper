import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export default function DriveStatus() {
  const [drives, setDrives] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.drives()
      .then(setDrives)
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return <div className="panel"><h2>Drive Status</h2><div className="empty-state">Error loading drives: {error}</div></div>;
  }

  if (drives === null) {
    return <div className="panel"><h2>Drive Status</h2><div className="empty-state">Loading...</div></div>;
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
        <div className="panel" key={drive.id}>
          <h2>{drive.label || drive.device_path} ({drive.drive_type?.toUpperCase()})</h2>
          <p>Device: {drive.device_path}</p>
          {drive.current_job ? (
            <p>
              Job #{drive.current_job.id} — disc #{drive.current_job.disc_id} —{" "}
              <span className={`status-pill ${drive.current_job.status}`}>{drive.current_job.status}</span>
            </p>
          ) : (
            <p><span className="status-pill idle">idle</span></p>
          )}
        </div>
      ))}
    </div>
  );
}
