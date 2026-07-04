import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

function IssueSection({ title, count, children, headerAction }) {
  const [open, setOpen] = useState(true);
  if (count === 0) return null;
  return (
    <div className="audit-section">
      <div className="audit-section-header">
        <button className="audit-section-toggle" onClick={() => setOpen((o) => !o)}>
          <span className="audit-section-title">{title}</span>
          <span className="audit-issue-count">{count}</span>
          <span className="audit-chevron">{open ? "▾" : "▸"}</span>
        </button>
        {headerAction && (
          <div className="audit-section-header-action">{headerAction}</div>
        )}
      </div>
      {open && <div className="audit-section-body">{children}</div>}
    </div>
  );
}

function IssueTable({ columns, rows }) {
  return (
    <table className="audit-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j}>{cell}</td>
            ))}
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

  async function run(...args) {
    setLoading(true);
    setResult(null);
    try {
      const r = await apiFn(...args);
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

export default function Audit() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const dvdJobs = useAction(api.createMissingDvdEncodeJobs);
  const cdJobs = useAction(api.createMissingCdEncodeJobs);
  const fixAssociations = useAction(api.fixStaleDriveAssociations);
  const cleanWavDirs = useAction(api.cleanupOrphanedWavDirs);

  const runAudit = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getAudit()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    runAudit();
  }, [runAudit]);

  const { summary, dvd, cd, jobs } = data ?? {};

  async function handleDvdJobs() {
    await dvdJobs.run();
    runAudit();
  }

  async function handleCdJobs() {
    await cdJobs.run();
    runAudit();
  }

  async function handleFixAssociations() {
    await fixAssociations.run();
    runAudit();
  }

  async function handleCleanWavDirs() {
    if (!window.confirm("Delete all orphaned WAV directories? This cannot be undone.")) return;
    await cleanWavDirs.run();
    runAudit();
  }

  return (
    <div>
      <div className="panel">
        <div className="panel-heading-row">
          <h2>Audit</h2>
          <button className="audit-run-btn" onClick={runAudit} disabled={loading}>
            {loading ? "Running…" : "Run Audit"}
          </button>
        </div>

        {error && (
          <div className="empty-state" style={{ color: "var(--error)" }}>
            Error: {error}
          </div>
        )}
        {!data && !error && <div className="empty-state">Loading…</div>}

        {summary && (
          <div className="audit-summary">
            <span
              className={`audit-summary-item${summary.dvd_issues > 0 ? " has-issues" : " clean"}`}
            >
              DVD: {summary.dvd_issues} issue{summary.dvd_issues !== 1 ? "s" : ""}
            </span>
            <span
              className={`audit-summary-item${summary.cd_issues > 0 ? " has-issues" : " clean"}`}
            >
              CD: {summary.cd_issues} issue{summary.cd_issues !== 1 ? "s" : ""}
            </span>
            <span
              className={`audit-summary-item${summary.job_issues > 0 ? " has-issues" : " clean"}`}
            >
              Jobs: {summary.job_issues} issue{summary.job_issues !== 1 ? "s" : ""}
            </span>
          </div>
        )}
      </div>

      {summary?.total_issues === 0 && (
        <div className="panel audit-all-clear">
          <span className="audit-checkmark">✓</span> No issues found
        </div>
      )}

      {dvd && (
        <>
          <IssueSection
            title="DVD: Duplicate disc fingerprints"
            count={dvd.duplicate_discs.length}
          >
            <IssueTable
              columns={["Fingerprint", "Disc IDs", "Titles", "Statuses"]}
              rows={dvd.duplicate_discs.map((r) => [
                <code key="fp">{r.fingerprint}</code>,
                r.disc_ids.join(", "),
                r.titles.join(", "),
                r.statuses.join(", "),
              ])}
            />
          </IssueSection>

          <IssueSection
            title="DVD: Missing ISO files"
            count={dvd.missing_iso_files.length}
          >
            <IssueTable
              columns={["Disc ID", "Title", "Status", "raw_path"]}
              rows={dvd.missing_iso_files.map((r) => [
                r.disc_id,
                r.title,
                r.status,
                <code key="rp">{r.raw_path}</code>,
              ])}
            />
          </IssueSection>

          <IssueSection
            title="DVD: Orphaned raw directories"
            count={dvd.orphaned_iso_dirs.length}
          >
            <IssueTable
              columns={["Directory", "Path", "Files"]}
              rows={dvd.orphaned_iso_dirs.map((r) => [
                r.dir_name,
                <code key="path">{r.path}</code>,
                r.files.length > 0 ? r.files.join(", ") : <em>empty</em>,
              ])}
            />
          </IssueSection>

          <IssueSection
            title="DVD: Missing raw_path (ripped/done)"
            count={dvd.null_raw_path.length}
          >
            <IssueTable
              columns={["Disc ID", "Title", "Status"]}
              rows={dvd.null_raw_path.map((r) => [r.disc_id, r.title, r.status])}
            />
          </IssueSection>

          <IssueSection
            title="DVD: Stale drive associations"
            count={dvd.stale_drive_associations.length}
            headerAction={
              <ActionRow
                loading={fixAssociations.loading}
                loadingLabel="Fixing…"
                onClick={handleFixAssociations}
                label="Fix Stale Associations"
                result={fixAssociations.result}
                formatResult={(r) => `Fixed ${r.fixed} association${r.fixed !== 1 ? "s" : ""}`}
              />
            }
          >
            <IssueTable
              columns={["Disc ID", "Title", "Status", "Drive ID", "Drive Label"]}
              rows={dvd.stale_drive_associations.map((r) => [
                r.disc_id,
                r.title,
                r.status,
                r.drive_id,
                r.drive_label || "—",
              ])}
            />
          </IssueSection>

          <IssueSection
            title="DVD: Missing encode jobs"
            count={dvd.missing_encode_jobs.length}
            headerAction={
              <ActionRow
                loading={dvdJobs.loading}
                loadingLabel="Creating…"
                onClick={handleDvdJobs}
                label="Create Missing DVD Encode Jobs"
                result={dvdJobs.result}
                formatResult={(r) => `Created ${r.jobs_created} job${r.jobs_created !== 1 ? "s" : ""}`}
              />
            }
          >
            <IssueTable
              columns={["Disc ID", "Title", "Missing Profiles"]}
              rows={dvd.missing_encode_jobs.map((r) => [
                r.disc_id,
                r.temp_name,
                r.missing_profiles.map((p) => p.name).join(", "),
              ])}
            />
          </IssueSection>
        </>
      )}

      {cd && (
        <>
          <IssueSection
            title="CD: Duplicate disc fingerprints"
            count={cd.duplicate_discs.length}
          >
            <IssueTable
              columns={["Fingerprint", "Disc IDs", "Titles", "Statuses"]}
              rows={cd.duplicate_discs.map((r) => [
                <code key="fp">{r.fingerprint}</code>,
                r.disc_ids.join(", "),
                r.titles.join(", "),
                r.statuses.join(", "),
              ])}
            />
          </IssueSection>

          <IssueSection
            title="CD: Missing WAV files"
            count={cd.missing_wav_files.length}
          >
            <IssueTable
              columns={["Disc ID", "Title", "Track", "WAV file", "raw_path"]}
              rows={cd.missing_wav_files.map((r) => [
                r.disc_id,
                r.title,
                r.track_number,
                <code key="wav">{r.wav_filename}</code>,
                <code key="rp">{r.raw_path}</code>,
              ])}
            />
          </IssueSection>

          <IssueSection
            title="CD: Orphaned WAV directories"
            count={cd.orphaned_wav_dirs.length}
            headerAction={
              <ActionRow
                loading={cleanWavDirs.loading}
                loadingLabel="Deleting…"
                onClick={handleCleanWavDirs}
                label="Delete Orphaned Directories"
                result={cleanWavDirs.result}
                formatResult={(r) =>
                  r.errors > 0
                    ? `Deleted ${r.deleted}, ${r.errors} error${r.errors !== 1 ? "s" : ""}`
                    : `Deleted ${r.deleted} director${r.deleted !== 1 ? "ies" : "y"}`
                }
              />
            }
          >
            <IssueTable
              columns={["Directory", "Path"]}
              rows={cd.orphaned_wav_dirs.map((r) => [
                r.dir_name,
                <code key="path">{r.path}</code>,
              ])}
            />
          </IssueSection>

          <IssueSection
            title="CD: Tracks with missing WAV filename"
            count={cd.tracks_missing_wav_filename.length}
          >
            <IssueTable
              columns={["Disc ID", "Title", "Status", "Tracks", "Missing"]}
              rows={cd.tracks_missing_wav_filename.map((r) => [
                r.disc_id,
                r.title,
                r.status,
                r.track_count,
                r.missing_count,
              ])}
            />
          </IssueSection>

          <IssueSection
            title="CD: Missing encode jobs"
            count={cd.missing_encode_jobs.length}
            headerAction={
              <ActionRow
                loading={cdJobs.loading}
                loadingLabel="Creating…"
                onClick={handleCdJobs}
                label="Create Missing CD Encode Jobs"
                result={cdJobs.result}
                formatResult={(r) => `Created ${r.jobs_created} job${r.jobs_created !== 1 ? "s" : ""}`}
              />
            }
          >
            <IssueTable
              columns={["Disc ID", "Title", "Missing Profiles", "Tracks Affected"]}
              rows={cd.missing_encode_jobs.map((r) => [
                r.disc_id,
                r.temp_name,
                r.missing_profiles.map((p) => p.name).join(", "),
                r.affected_tracks,
              ])}
            />
          </IssueSection>
        </>
      )}

      {jobs && (
        <IssueSection
          title="Jobs: Stuck in running state"
          count={jobs.stuck_running_jobs.length}
        >
          <IssueTable
            columns={["Job ID", "Disc ID", "Title", "Drive ID", "Started At"]}
            rows={jobs.stuck_running_jobs.map((r) => [
              r.job_id,
              r.disc_id,
              r.disc_title,
              r.drive_id ?? "—",
              r.started_at
                ? new Date(r.started_at).toLocaleString("en-GB", {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                : "—",
            ])}
          />
        </IssueSection>
      )}
    </div>
  );
}
