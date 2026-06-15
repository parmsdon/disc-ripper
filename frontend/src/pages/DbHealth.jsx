import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export default function DbHealth() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(e.message));
  }, []);

  if (error) {
    return <div className="panel"><h2>DB Health</h2><div className="empty-state">Error: {error}</div></div>;
  }

  if (!health) {
    return <div className="panel"><h2>DB Health</h2><div className="empty-state">Loading...</div></div>;
  }

  return (
    <div>
      <div className="panel">
        <h2>Library Counts</h2>
        <div className="grid">
          <div className="stat">
            <div className="value">{health.counts.dvds}</div>
            <div className="label">DVDs</div>
          </div>
          <div className="stat">
            <div className="value">{health.counts.cds}</div>
            <div className="label">CDs</div>
          </div>
          <div className="stat">
            <div className="value">{health.counts.cd_tracks}</div>
            <div className="label">CD Tracks</div>
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>Issues</h2>
        <div className="grid">
          <div className="stat">
            <div className="value">{health.db_health.unmatched_dvds}</div>
            <div className="label">DVDs not matched to My Movies</div>
          </div>
          <div className="stat">
            <div className="value">{health.db_health.discs_needing_rerip}</div>
            <div className="label">Discs needing re-rip</div>
          </div>
        </div>
      </div>
    </div>
  );
}
