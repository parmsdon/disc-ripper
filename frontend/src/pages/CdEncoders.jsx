import React, { useEffect, useState } from "react";
import { api } from "../api/client";

export default function CdEncoders() {
  const [profiles, setProfiles] = useState(null);

  useEffect(() => {
    api.encodeProfiles().then(setProfiles).catch(() => setProfiles([]));
  }, []);

  return (
    <div className="panel">
      <h2>CD Encoders</h2>
      <div className="empty-state">
        No encode jobs yet. CD encoding (WAV → MP3/FLAC) is a later phase —
        this tab will show active and queued CD encode jobs once that's built.
      </div>
      {profiles && profiles.length > 0 && (
        <>
          <h2 style={{ marginTop: 16 }}>Configured Profiles</h2>
          <ul>
            {profiles.filter(p => p.target === "audio").map((p) => (
              <li key={p.id}>{p.name} — {p.format}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
