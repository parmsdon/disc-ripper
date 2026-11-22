import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import CdIdentifyPanel from "./CdIdentifyPanel.jsx";

function IssueTable({ columns, rows }) {
  return (
    <table className="audit-table">
      <thead>
        <tr>
          {columns.map((c) => <th key={c}>{c}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => <td key={j}>{cell}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ActionRow({ loading, loadingLabel, onClick, label, result, formatResult }) {
  return (
    <div className="audit-action-row">
      <button className="audit-action-btn" onClick={onClick} disabled={loading}>
        {loading ? loadingLabel : label}
      </button>
      {result && !result.error && (
        <span className="audit-action-result">{formatResult(result)}</span>
      )}
      {result?.error && (
        <span className="audit-action-result audit-action-error">Error: {result.error}</span>
      )}
    </div>
  );
}

function useAction(apiFn) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  async function run() {
    setLoading(true);
    setResult(null);
    try {
      const r = await apiFn();
      setResult(r);
      return r;
    } catch (e) {
      setResult({ error: e.message });
    } finally {
      setLoading(false);
    }
  }

  return { loading, result, run };
}

function CheckRow({ groupLabel, name, count, open, onToggle, action, groupStart = false, issueLabel = "issue", children }) {
  const isPending = count === undefined;
  const hasIssues = !isPending && count > 0;

  return (
    <div className={`audit-check-card${groupStart ? " audit-check-card--group-start" : ""}`}>
      <div
        className={`audit-check-row${hasIssues ? " audit-check-row--clickable" : ""}`}
        onClick={hasIssues ? onToggle : undefined}
      >
        <div className="audit-check-group">{groupLabel}</div>
        <div className="audit-check-name">
          {name}
          {hasIssues && (
            <span className="audit-check-chevron">{open ? "▾" : "▸"}</span>
          )}
        </div>
        <div className="audit-check-status">
          {isPending ? (
            <span className="audit-check-pill audit-check-pill--pending">—</span>
          ) : count === 0 ? (
            <span className="audit-check-pill audit-check-pill--clear">All clear ✓</span>
          ) : (
            <span className="audit-check-pill audit-check-pill--issues">
              {count} {count === 1 ? issueLabel : `${issueLabel}s`}
            </span>
          )}
        </div>
      </div>
      {hasIssues && open && (
        <div className="audit-check-detail">
          {action && <div className="audit-check-detail-action">{action}</div>}
          <div className="audit-check-detail-body">{children}</div>
        </div>
      )}
    </div>
  );
}

export default function Audit() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [openChecks, setOpenChecks] = useState(new Set());
  const [artworkReidentifyDisc, setArtworkReidentifyDisc] = useState(null);
  const [artworkReidentifyLoadingId, setArtworkReidentifyLoadingId] = useState(null);

  const dvdJobs      = useAction(api.createMissingDvdEncodeJobs);
  const cdJobs       = useAction(api.createMissingCdEncodeJobs);
  const fixDvdAssoc  = useAction(api.fixStaleDvdDriveAssociations);
  const fixCdAssoc   = useAction(api.fixStaleCdDriveAssociations);
  const cleanWavDirs = useAction(api.cleanupOrphanedWavDirs);

  function computeOpen(d) {
    const keys = new Set();
    if (d.dvd?.duplicate_discs?.length > 0)            keys.add("dvd-dup-fps");
    if (d.dvd?.missing_iso_files?.length > 0)          keys.add("dvd-missing-iso");
    if (d.dvd?.orphaned_iso_dirs?.length > 0)          keys.add("dvd-orphaned-iso");
    if (d.dvd?.null_raw_path?.length > 0)              keys.add("dvd-null-raw");
    if (d.dvd?.stale_drive_associations?.length > 0)   keys.add("dvd-stale-assoc");
    if (d.dvd?.missing_encode_jobs?.length > 0)        keys.add("dvd-missing-jobs");
    if (d.dvd?.duplicate_temp_names?.length > 0)       keys.add("dvd-dup-titles");
    if (d.cd?.duplicate_discs?.length > 0)             keys.add("cd-dup-fps");
    if (d.cd?.missing_wav_files?.length > 0)           keys.add("cd-missing-wav");
    if (d.cd?.orphaned_wav_dirs?.length > 0)           keys.add("cd-orphaned-wav");
    if (d.cd?.stale_drive_associations?.length > 0)    keys.add("cd-stale-assoc");
    if (d.cd?.tracks_missing_wav_filename?.length > 0) keys.add("cd-missing-fn");
    if (d.cd?.missing_encode_jobs?.length > 0)         keys.add("cd-missing-jobs");
    if (d.jobs?.stuck_running_jobs?.length > 0)        keys.add("jobs-stuck");
    if (d.catalog?.duplicate_needs_review?.length > 0) keys.add("catalog-dups");
    return keys;
  }

  const runAudit = useCallback(() => {
    setLoading(true);
    setError(null);
    api.getAudit()
      .then((d) => { setData(d); setOpenChecks(computeOpen(d)); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { runAudit(); }, [runAudit]);

  function toggle(key) {
    setOpenChecks((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  const { summary, dvd, cd, jobs, catalog: cat, import: imp } = data ?? {};

  async function handleDvdJobs()      { await dvdJobs.run();      runAudit(); }
  async function handleCdJobs()       { await cdJobs.run();       runAudit(); }
  async function handleFixDvdAssoc()  { await fixDvdAssoc.run();  runAudit(); }
  async function handleFixCdAssoc()   { await fixCdAssoc.run();   runAudit(); }
  async function handleCleanWavDirs() { await cleanWavDirs.run(); runAudit(); }

  async function handleArtworkReidentify(discId) {
    setArtworkReidentifyLoadingId(discId);
    try {
      const disc = await api.disc(discId);
      setArtworkReidentifyDisc(disc);
    } finally {
      setArtworkReidentifyLoadingId(null);
    }
  }

  const o   = (key) => openChecks.has(key);
  const tog = (key) => () => toggle(key);

  return (
    <div>
      <div className="panel" style={{ marginBottom: 12 }}>
        <div className="panel-heading-row">
          <h2>Audit</h2>
          <button className="audit-run-btn" onClick={runAudit} disabled={loading}>
            {loading ? "Running…" : "Run Audit"}
          </button>
        </div>
        {error && (
          <div className="empty-state" style={{ color: "var(--error)" }}>Error: {error}</div>
        )}
        {summary && (
          <div className="audit-summary">
            <span className={`audit-summary-item${summary.dvd_issues > 0 ? " has-issues" : " clean"}`}>
              DVD: {summary.dvd_issues} issue{summary.dvd_issues !== 1 ? "s" : ""}
            </span>
            <span className={`audit-summary-item${summary.cd_issues > 0 ? " has-issues" : " clean"}`}>
              CD: {summary.cd_issues} issue{summary.cd_issues !== 1 ? "s" : ""}
            </span>
            <span className={`audit-summary-item${summary.job_issues > 0 ? " has-issues" : " clean"}`}>
              Jobs: {summary.job_issues} issue{summary.job_issues !== 1 ? "s" : ""}
            </span>
            <span className={`audit-summary-item${summary.catalog_issues > 0 ? " has-issues" : " clean"}`}>
              Catalog: {summary.catalog_issues} issue{summary.catalog_issues !== 1 ? "s" : ""}
            </span>
            {imp && (
              <span className={`audit-summary-item${imp.imported_unlinked > 0 ? " has-issues" : " clean"}`}
                style={imp.imported_unlinked > 0 ? {
                  color: "var(--warn)",
                  background: "rgba(217,161,60,0.1)",
                  borderColor: "rgba(217,161,60,0.3)",
                } : undefined}>
                {imp.imported_unlinked > 0
                  ? `Import: ${imp.imported_unlinked} unlinked`
                  : "Import: all linked"}
              </span>
            )}
          </div>
        )}
      </div>

      <div className="audit-layout">

        {/* ── DVD ── */}
        <CheckRow groupLabel="DVD" name="Duplicate disc fingerprints"
          count={dvd?.duplicate_discs?.length}
          open={o("dvd-dup-fps")} onToggle={tog("dvd-dup-fps")}>
          <IssueTable columns={["Fingerprint", "Disc IDs", "Titles", "Statuses"]}
            rows={(dvd?.duplicate_discs ?? []).map((r) => [
              <code key="fp">{r.fingerprint}</code>,
              r.disc_ids.join(", "), r.titles.join(", "), r.statuses.join(", "),
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Missing ISO files"
          count={dvd?.missing_iso_files?.length}
          open={o("dvd-missing-iso")} onToggle={tog("dvd-missing-iso")}>
          <IssueTable columns={["Disc ID", "Title", "Status", "raw_path"]}
            rows={(dvd?.missing_iso_files ?? []).map((r) => [
              r.disc_id, r.title, r.status,
              <code key="rp">{r.raw_path}</code>,
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Orphaned raw directories"
          count={dvd?.orphaned_iso_dirs?.length}
          open={o("dvd-orphaned-iso")} onToggle={tog("dvd-orphaned-iso")}>
          <IssueTable columns={["Directory", "Path", "Files"]}
            rows={(dvd?.orphaned_iso_dirs ?? []).map((r) => [
              r.dir_name,
              <code key="path">{r.path}</code>,
              r.files.length > 0 ? r.files.join(", ") : <em>empty</em>,
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Missing raw_path (ripped/done)"
          count={dvd?.null_raw_path?.length}
          open={o("dvd-null-raw")} onToggle={tog("dvd-null-raw")}>
          <IssueTable columns={["Disc ID", "Title", "Status"]}
            rows={(dvd?.null_raw_path ?? []).map((r) => [r.disc_id, r.title, r.status])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Stale drive associations"
          count={dvd?.stale_drive_associations?.length}
          open={o("dvd-stale-assoc")} onToggle={tog("dvd-stale-assoc")}
          action={
            <ActionRow loading={fixDvdAssoc.loading} loadingLabel="Fixing…"
              onClick={handleFixDvdAssoc} label="Fix Stale Associations"
              result={fixDvdAssoc.result}
              formatResult={(r) => `Fixed ${r.fixed} association${r.fixed !== 1 ? "s" : ""}`} />
          }>
          <IssueTable columns={["Disc ID", "Title", "Status", "Drive ID", "Drive Label"]}
            rows={(dvd?.stale_drive_associations ?? []).map((r) => [
              r.disc_id, r.title, r.status, r.drive_id, r.drive_label || "—",
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Missing encode jobs"
          count={dvd?.missing_encode_jobs?.length}
          open={o("dvd-missing-jobs")} onToggle={tog("dvd-missing-jobs")}
          action={
            <ActionRow loading={dvdJobs.loading} loadingLabel="Creating…"
              onClick={handleDvdJobs} label="Create Missing DVD Encode Jobs"
              result={dvdJobs.result}
              formatResult={(r) => `Created ${r.jobs_created} job${r.jobs_created !== 1 ? "s" : ""}`} />
          }>
          <IssueTable columns={["Disc ID", "Title", "Missing Profiles"]}
            rows={(dvd?.missing_encode_jobs ?? []).map((r) => [
              r.disc_id, r.temp_name,
              r.missing_profiles.map((p) => p.name).join(", "),
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Duplicate working titles"
          count={dvd?.duplicate_temp_names?.length}
          open={o("dvd-dup-titles")} onToggle={tog("dvd-dup-titles")}>
          <IssueTable columns={["Working Title", "Count", "Disc IDs"]}
            rows={(dvd?.duplicate_temp_names ?? []).map((r) => [
              r.temp_name, r.count, r.disc_ids.join(", "),
            ])} />
        </CheckRow>

        {/* ── CD ── */}
        <CheckRow groupLabel="CD" name="Duplicate disc fingerprints"
          groupStart count={cd?.duplicate_discs?.length}
          open={o("cd-dup-fps")} onToggle={tog("cd-dup-fps")}>
          <IssueTable columns={["Fingerprint", "Disc IDs", "Titles", "Statuses"]}
            rows={(cd?.duplicate_discs ?? []).map((r) => [
              <code key="fp">{r.fingerprint}</code>,
              r.disc_ids.join(", "), r.titles.join(", "), r.statuses.join(", "),
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Missing WAV files"
          count={cd?.missing_wav_files?.length}
          open={o("cd-missing-wav")} onToggle={tog("cd-missing-wav")}>
          <IssueTable columns={["Disc ID", "Title", "Track", "WAV file", "raw_path"]}
            rows={(cd?.missing_wav_files ?? []).map((r) => [
              r.disc_id, r.title, r.track_number,
              <code key="wav">{r.wav_filename}</code>,
              <code key="rp">{r.raw_path}</code>,
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Orphaned WAV directories"
          count={cd?.orphaned_wav_dirs?.length}
          open={o("cd-orphaned-wav")} onToggle={tog("cd-orphaned-wav")}
          action={
            <ActionRow loading={cleanWavDirs.loading} loadingLabel="Deleting…"
              onClick={handleCleanWavDirs} label="Delete Orphaned Directories"
              result={cleanWavDirs.result}
              formatResult={(r) =>
                r.errors > 0
                  ? `Deleted ${r.deleted}, ${r.errors} error${r.errors !== 1 ? "s" : ""}`
                  : `Deleted ${r.deleted} director${r.deleted !== 1 ? "ies" : "y"}`
              } />
          }>
          <IssueTable columns={["Directory", "Path"]}
            rows={(cd?.orphaned_wav_dirs ?? []).map((r) => [
              r.dir_name,
              <code key="path">{r.path}</code>,
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Stale drive associations"
          count={cd?.stale_drive_associations?.length}
          open={o("cd-stale-assoc")} onToggle={tog("cd-stale-assoc")}
          action={
            <ActionRow loading={fixCdAssoc.loading} loadingLabel="Fixing…"
              onClick={handleFixCdAssoc} label="Fix Stale Associations"
              result={fixCdAssoc.result}
              formatResult={(r) => `Fixed ${r.fixed} association${r.fixed !== 1 ? "s" : ""}`} />
          }>
          <IssueTable columns={["Disc ID", "Title", "Status", "Drive ID", "Drive Label"]}
            rows={(cd?.stale_drive_associations ?? []).map((r) => [
              r.disc_id, r.title, r.status, r.drive_id, r.drive_label || "—",
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Tracks with missing WAV filename"
          count={cd?.tracks_missing_wav_filename?.length}
          open={o("cd-missing-fn")} onToggle={tog("cd-missing-fn")}>
          <IssueTable columns={["Disc ID", "Title", "Status", "Tracks", "Missing"]}
            rows={(cd?.tracks_missing_wav_filename ?? []).map((r) => [
              r.disc_id, r.title, r.status, r.track_count, r.missing_count,
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Missing encode jobs"
          count={cd?.missing_encode_jobs?.length}
          open={o("cd-missing-jobs")} onToggle={tog("cd-missing-jobs")}
          action={
            <ActionRow loading={cdJobs.loading} loadingLabel="Creating…"
              onClick={handleCdJobs} label="Create Missing CD Encode Jobs"
              result={cdJobs.result}
              formatResult={(r) => `Created ${r.jobs_created} job${r.jobs_created !== 1 ? "s" : ""}`} />
          }>
          <IssueTable columns={["Disc ID", "Title", "Missing Profiles", "Tracks Affected"]}
            rows={(cd?.missing_encode_jobs ?? []).map((r) => [
              r.disc_id, r.temp_name,
              r.missing_profiles.map((p) => p.name).join(", "),
              r.affected_tracks,
            ])} />
        </CheckRow>

        <CheckRow groupLabel="" name="Missing artwork"
          count={cd?.missing_artwork?.length}
          issueLabel="missing"
          open={o("cd-missing-artwork")} onToggle={tog("cd-missing-artwork")}>
          <IssueTable columns={["Disc ID", "Album", "Artist", "Action"]}
            rows={(cd?.missing_artwork ?? []).map((r) => [
              r.disc_id,
              r.title,
              r.artist || "—",
              <button
                key={r.disc_id}
                className="catalogue-reidentify-btn"
                onClick={() => handleArtworkReidentify(r.disc_id)}
                disabled={artworkReidentifyLoadingId === r.disc_id}
              >
                {artworkReidentifyLoadingId === r.disc_id ? "…" : "Re-identify"}
              </button>,
            ])} />
        </CheckRow>

        {/* ── Jobs ── */}
        <CheckRow groupLabel="Jobs" name="Stuck in running state"
          groupStart count={jobs?.stuck_running_jobs?.length}
          open={o("jobs-stuck")} onToggle={tog("jobs-stuck")}>
          <IssueTable columns={["Job ID", "Disc ID", "Title", "Drive ID", "Started At"]}
            rows={(jobs?.stuck_running_jobs ?? []).map((r) => [
              r.job_id, r.disc_id, r.disc_title, r.drive_id ?? "—",
              r.started_at
                ? new Date(r.started_at).toLocaleString("en-GB", {
                    day: "numeric", month: "short", year: "numeric",
                    hour: "2-digit", minute: "2-digit",
                  })
                : "—",
            ])} />
        </CheckRow>

        {/* ── Catalog ── */}
        <CheckRow groupLabel="Catalog" name="Duplicate entries needing review"
          groupStart count={cat?.duplicate_needs_review?.length}
          open={o("catalog-dups")} onToggle={tog("catalog-dups")}>
          {(cat?.duplicate_needs_review ?? []).map((group) => (
            <div key={group.group_key} className="audit-dup-group">
              <IssueTable
                columns={["#", "Title", "Year", "Edition", "Std", "Regions", "Role", "Library suffix"]}
                rows={group.entries.map((e) => [
                  e.collection_number != null ? `#${e.collection_number}` : "—",
                  e.title, e.year ?? "—",
                  e.edition || <em>standard</em>,
                  e.video_standard || "—", e.region_codes || "—",
                  e.role,
                  e.link_suffix ? <code key="ls">{e.link_suffix}</code> : <em>plain name</em>,
                ])}
              />
            </div>
          ))}
        </CheckRow>

        {/* ── Import (informational) ── */}
        {imp && (
          <div className="audit-check-card audit-check-card--group-start">
            <div className="audit-check-row">
              <div className="audit-check-group">Import</div>
              <div className="audit-import-counts">
                <span>
                  <strong>{imp.legacy_waiting}</strong>
                  <span className="text-dim"> waiting in legacy/</span>
                </span>
                <span>
                  <strong>{imp.imported_discs}</strong>
                  <span className="text-dim"> imported discs</span>
                </span>
                <span>
                  <strong>{imp.imported_unlinked}</strong>
                  <span className="text-dim"> unlinked</span>
                  {imp.imported_unlinked > 0 && (
                    <span className="text-dim"> — link in Identification tab</span>
                  )}
                </span>
              </div>
              <div className="audit-check-status">
                {imp.imported_unlinked > 0 ? (
                  <span className="audit-check-pill audit-check-pill--issues">
                    {imp.imported_unlinked} unlinked
                  </span>
                ) : (
                  <span className="audit-check-pill audit-check-pill--clear">All clear ✓</span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {artworkReidentifyDisc && (
        <CdIdentifyPanel
          disc={artworkReidentifyDisc}
          onConfirm={() => { setArtworkReidentifyDisc(null); runAudit(); }}
          onSkip={() => setArtworkReidentifyDisc(null)}
        />
      )}
    </div>
  );
}
