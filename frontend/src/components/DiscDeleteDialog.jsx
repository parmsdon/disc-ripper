import React, { useEffect, useState } from "react";
import { api } from "../api/client";

function fmt(mb) {
  if (mb === null || mb === undefined) return "";
  if (mb >= 1024) return ` (${(mb / 1024).toFixed(1)} GB)`;
  if (mb > 0) return ` (${mb} MB)`;
  return "";
}

export default function DiscDeleteDialog({ discId, onClose, onDeleted }) {
  const [preview, setPreview] = useState(null);
  const [loadErr, setLoadErr] = useState(null);
  const [moveToLegacy, setMoveToLegacy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState(null);

  useEffect(() => {
    setLoadErr(null);
    api.discDeletePreview(discId)
      .then((p) => { setPreview(p); if (p.legacy_option) setMoveToLegacy(false); })
      .catch((e) => setLoadErr(e.message));
  }, [discId]);

  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleConfirm() {
    setSubmitErr(null);
    setSubmitting(true);
    try {
      await api.deleteDisc(discId, moveToLegacy);
      onDeleted();
    } catch (e) {
      setSubmitErr(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  const isBlocked = preview?.blocking_rip || preview?.blocking_encode;

  return (
    <div className="identify-panel-overlay" onClick={onClose}>
      <div className="health-modal delete-disc-modal" onClick={(e) => e.stopPropagation()}>
        <div className="health-modal-header">
          <span className="health-modal-title">Delete disc</span>
          <button className="identify-panel-close" onClick={onClose}>×</button>
        </div>

        <div className="health-modal-body delete-disc-body">
          {!preview && !loadErr && (
            <div className="empty-state">Loading…</div>
          )}

          {loadErr && (
            <div className="empty-state" style={{ color: "var(--error)" }}>Error: {loadErr}</div>
          )}

          {preview && (
            <>
              <p className="delete-disc-name">
                {preview.disc_name}
                {preview.disc_type && (
                  <span className="delete-disc-type">{preview.disc_type.toUpperCase()}</span>
                )}
              </p>

              {preview.blocking_rip && (
                <div className="delete-disc-blocked">
                  Disc is currently ripping — cancel the rip first.
                </div>
              )}
              {!preview.blocking_rip && preview.blocking_encode && (
                <div className="delete-disc-blocked">
                  An encode is in progress — it will be stopped and its output discarded.
                </div>
              )}

              {!preview.blocking_rip && (
                <>
                  <ul className="delete-disc-list">
                    {preview.rip_job_count > 0 && (
                      <li>{preview.rip_job_count} rip job{preview.rip_job_count !== 1 ? "s" : ""}</li>
                    )}
                    {preview.encode_job_count > 0 && (
                      <li>
                        {preview.encode_job_count} encode job{preview.encode_job_count !== 1 ? "s" : ""}
                        {preview.encode_output_count > 0
                          ? ` · ${preview.encode_output_count} output file${preview.encode_output_count !== 1 ? "s" : ""}${fmt(preview.encode_output_mb)}`
                          : ""}
                      </li>
                    )}
                    {preview.cd_track_count > 0 && (
                      <li>{preview.cd_track_count} track record{preview.cd_track_count !== 1 ? "s" : ""}</li>
                    )}
                    {preview.lookup_candidate_count > 0 && (
                      <li>{preview.lookup_candidate_count} lookup candidate{preview.lookup_candidate_count !== 1 ? "s" : ""}</li>
                    )}
                    {preview.has_cover && <li>cover image</li>}
                    {preview.has_raw_folder && (
                      <li>
                        raw rip folder{fmt(preview.raw_folder_mb)}
                        {preview.legacy_option && moveToLegacy ? " (ISO will be moved to legacy/)" : ""}
                      </li>
                    )}
                  </ul>

                  <p className="delete-disc-note">Rip log entries are kept.</p>

                  {preview.legacy_option && (
                    <div className="delete-disc-legacy">
                      <label>
                        <input
                          type="radio"
                          name="iso-action"
                          checked={!moveToLegacy}
                          onChange={() => setMoveToLegacy(false)}
                        />
                        {" "}Delete the ISO
                      </label>
                      <label>
                        <input
                          type="radio"
                          name="iso-action"
                          checked={moveToLegacy}
                          onChange={() => setMoveToLegacy(true)}
                        />
                        {" "}Move ISO back to <code>legacy/</code> ({preview.legacy_option})
                      </label>
                    </div>
                  )}

                  {submitErr && (
                    <div className="delete-disc-submit-err">{submitErr}</div>
                  )}

                  <div className="delete-disc-actions">
                    <button className="delete-disc-cancel-btn" onClick={onClose}>
                      Cancel
                    </button>
                    <button
                      className="delete-disc-confirm-btn"
                      onClick={handleConfirm}
                      disabled={submitting}
                    >
                      {submitting ? "Deleting…" : "Delete"}
                    </button>
                  </div>
                </>
              )}

              {preview.blocking_rip && (
                <div className="delete-disc-actions">
                  <button className="delete-disc-cancel-btn" onClick={onClose}>Close</button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
