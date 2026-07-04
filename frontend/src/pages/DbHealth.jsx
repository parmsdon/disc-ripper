import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso + "Z").toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric",
  });
}

function TypeBadge({ type }) {
  if (type === "dvd") return <span className="status-pill good">DVD</span>;
  if (type === "cd")  return <span className="status-pill running">CD</span>;
  return <span className="status-pill">{type}</span>;
}

function Modal({ title, onClose, loading, children }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="identify-panel-overlay" onClick={onClose}>
      <div className="health-modal" onClick={(e) => e.stopPropagation()}>
        <div className="health-modal-header">
          <span className="health-modal-title">{title}</span>
          <button className="identify-panel-close" onClick={onClose}>×</button>
        </div>
        <div className="health-modal-body">
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : children}
        </div>
      </div>
    </div>
  );
}

function Stat({ value, label, tone, onClick }) {
  const clickable = onClick && value > 0;
  return (
    <div
      className={`stat${tone ? ` stat-${tone}` : ""}${clickable ? " stat-clickable" : ""}`}
      onClick={clickable ? onClick : undefined}
    >
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

// Returns a tone string based on whether value is zero.
function t(value, nonzeroTone, zeroTone = null) {
  return value > 0 ? nonzeroTone : zeroTone;
}

export default function DbHealth() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [modal, setModal] = useState(null);
  const [modalData, setModalData] = useState(null);
  const [modalLoading, setModalLoading] = useState(false);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(e.message));
  }, []);

  const openModal = useCallback(async (type, fetcher, title) => {
    setModal({ type, title });
    setModalData(null);
    setModalLoading(true);
    try {
      setModalData(await fetcher());
    } catch (e) {
      setModalData({ error: e.message });
    } finally {
      setModalLoading(false);
    }
  }, []);

  const closeModal = useCallback(() => { setModal(null); setModalData(null); }, []);

  if (error) {
    return <div className="panel"><h2>Health</h2><div className="empty-state">Error: {error}</div></div>;
  }
  if (!health) {
    return <div className="panel"><h2>Health</h2><div className="empty-state">Loading…</div></div>;
  }

  const { library: lib, my_movies: mm, identification: id, quality: qual, musicbrainz: mb, pipeline, dvd_encodes, cd_encodes } = health;

  const pipelineActive =
    pipeline.currently_ripping > 0 ||
    pipeline.currently_building > 0 ||
    pipeline.currently_identifying > 0 ||
    pipeline.error_discs > 0;

  return (
    <div>
      {modal && (
        <Modal title={modal.title} onClose={closeModal} loading={modalLoading}>
          {modalData?.error && <div className="empty-state" style={{ color: "var(--error)" }}>Error: {modalData.error}</div>}
          {Array.isArray(modalData) && modalData.length === 0 && (
            <div className="empty-state">No records found.</div>
          )}
          {Array.isArray(modalData) && modalData.length > 0 && (
            <table className="audit-table">
              <thead>
                <tr>
                  {(modal.type === "pipeline-identifying" || modal.type === "pipeline-errors") && <th>Type</th>}
                  <th>Disc</th>
                  {modal.type === "pipeline-errors" ? <th>Error</th> : <th>Fingerprint</th>}
                  {modal.type === "pipeline-errors" ? <th>Created</th> : <th>Ripped</th>}
                </tr>
              </thead>
              <tbody>
                {modalData.map((r) => (
                  <tr key={r.disc_id}>
                    {(modal.type === "pipeline-identifying" || modal.type === "pipeline-errors") && (
                      <td><TypeBadge type={r.type} /></td>
                    )}
                    <td>
                      <div>{r.temp_name}</div>
                    </td>
                    {modal.type === "pipeline-errors" ? (
                      <td className="health-modal-error-msg">{r.error_message || "—"}</td>
                    ) : (
                      <td><code className="catalogue-mono">{r.disc_fingerprint || "—"}</code></td>
                    )}
                    <td>
                      {modal.type === "pipeline-errors"
                        ? formatDate(r.created_at)
                        : formatDate(r.ripped_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Modal>
      )}

      <div className="panel">
        <h2>Library</h2>
        <div className="grid">
          <Stat value={lib.dvd_count}      label="DVDs" />
          <Stat value={lib.cd_count}       label="CDs" />
          <Stat value={lib.cd_track_count} label="CD Tracks" />
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading-row">
          <h2>My Movies</h2>
          <span className="panel-subtitle">
            {mm.last_sync
              ? `Last synced: ${new Date(mm.last_sync).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}`
              : "Never synced"}
          </span>
        </div>
        <div className="grid">
          <Stat value={mm.catalog_count}     label="Catalog Entries" />
          <Stat value={mm.matched_to_ripped} label="Matched to Ripped Disc" tone={t(mm.matched_to_ripped, "good")} />
          <Stat value={mm.never_ripped}      label="Never Ripped" />
        </div>
      </div>

      <div className="panel">
        <h2>Identification</h2>
        <div className="grid">
          <Stat value={id.dvds_matched}       label="DVDs Matched"        tone={t(id.dvds_matched, "good")} />
          <Stat value={id.dvds_unmatched}     label="DVDs Unmatched"      tone={t(id.dvds_unmatched, "warn", "good")} />
          <Stat value={id.cds_identified}     label="CDs Identified"      tone={t(id.cds_identified, "good")} />
          <Stat value={id.cds_unidentified}   label="CDs Unidentified"    tone={t(id.cds_unidentified, "warn", "good")} />
          <Stat value={id.cd_tracks_titled}   label="CD Tracks Titled" />
          <Stat value={id.cd_tracks_untitled} label="CD Tracks Untitled"  tone={t(id.cd_tracks_untitled, "warn", "good")} />
        </div>
      </div>

      <div className="panel">
        <h2>Quality</h2>
        <div className="grid">
          <Stat value={qual.discs_needing_rerip} label="Needs Re-rip"      tone={t(qual.discs_needing_rerip, "warn", "good")} />
          <Stat value={qual.dirty_rips}          label="Dirty Rips"        tone={t(qual.dirty_rips, "warn", "good")} />
          <Stat value={qual.imperfect_tracks}    label="Imperfect Tracks"  tone={t(qual.imperfect_tracks, "warn", "good")} />
        </div>
      </div>

      <div className="panel">
        <h2>MusicBrainz</h2>
        <div className="grid">
          <Stat value={mb.cds_mb_found}     label="MB Found"     tone={t(mb.cds_mb_found, "good")} />
          <Stat
            value={mb.cds_mb_not_found}
            label="MB Not Found"
            tone={t(mb.cds_mb_not_found, "warn")}
            onClick={() => openModal("mb-not-found", api.getMbNotFound, "MusicBrainz — Not Found")}
          />
          <Stat value={mb.cds_mb_pending}   label="MB Pending"   tone={t(mb.cds_mb_pending, "info")} />
          <Stat
            value={mb.cds_mb_error}
            label="MB Error"
            tone={t(mb.cds_mb_error, "error")}
            onClick={() => openModal("mb-error", api.getMbError, "MusicBrainz — Error")}
          />
        </div>
      </div>

      {pipelineActive && (
        <div className="panel">
          <h2>Pipeline</h2>
          <div className="grid">
            <Stat value={pipeline.currently_ripping}  label="Ripping"    tone={t(pipeline.currently_ripping, "info")} />
            <Stat value={pipeline.currently_building} label="Building"   tone={t(pipeline.currently_building, "info")} />
            <Stat
              value={pipeline.currently_identifying}
              label="Identifying"
              tone={t(pipeline.currently_identifying, "info")}
              onClick={() => openModal("pipeline-identifying", api.getPipelineIdentifying, "Pipeline — Identifying")}
            />
            <Stat
              value={pipeline.error_discs}
              label="Errors"
              tone={t(pipeline.error_discs, "error")}
              onClick={() => openModal("pipeline-errors", api.getPipelineErrors, "Pipeline — Errors")}
            />
          </div>
        </div>
      )}

      <div className="panel">
        <h2>DVD Encodes</h2>
        <div className="grid">
          <Stat value={dvd_encodes.queued}   label="Queued" />
          <Stat value={dvd_encodes.running}  label="Running"  tone={t(dvd_encodes.running, "info")} />
          <Stat value={dvd_encodes.complete} label="Complete" tone={t(dvd_encodes.complete, "good")} />
          <Stat value={dvd_encodes.error}    label="Error"    tone={t(dvd_encodes.error, "error")} />
        </div>
      </div>

      <div className="panel">
        <h2>CD Encodes</h2>
        <div className="grid">
          <Stat value={cd_encodes.queued}   label="Queued" />
          <Stat value={cd_encodes.running}  label="Running"  tone={t(cd_encodes.running, "info")} />
          <Stat value={cd_encodes.complete} label="Complete" tone={t(cd_encodes.complete, "good")} />
          <Stat value={cd_encodes.error}    label="Error"    tone={t(cd_encodes.error, "error")} />
        </div>
      </div>
    </div>
  );
}
