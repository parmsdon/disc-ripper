import React from "react";

export default function DvdEncoders() {
  return (
    <div className="panel">
      <h2>DVD Encoders</h2>
      <div className="empty-state">
        No encode jobs yet. DVD encoding (ISO → MP4/MKV, main movie/episode
        extraction) is a later phase — this tab will show active and queued
        DVD encode jobs once that's built.
      </div>
    </div>
  );
}
