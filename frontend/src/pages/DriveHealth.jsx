import React, { useEffect, useState } from "react";
import { api } from "../api/client";

// ── Helpers ────────────────────────────────────────────────────────────────

function fmtDuration(secs) {
  if (secs == null) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function fmtPct(rate) {
  if (rate == null) return "—";
  return `${Math.round(rate * 100)}%`;
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// ── Outcome strip ──────────────────────────────────────────────────────────

const OUTCOME_COLOR = {
  clean:   "var(--good)",
  dirty:   "var(--warn)",
  failed:  "var(--error)",
  unknown: "var(--text-dim)",
};
const OUTCOME_LABEL = { clean: "Clean", dirty: "Dirty", failed: "Failed", unknown: "?" };

function OutcomeStrip({ outcomes }) {
  if (!outcomes || outcomes.length === 0) {
    return <span className="drive-health-no-data">No completed rips yet</span>;
  }
  return (
    <div className="outcome-strip" aria-label="Last 10 rip outcomes (newest first)">
      {outcomes.map((o, i) => (
        <span
          key={i}
          className="outcome-square"
          style={{ background: OUTCOME_COLOR[o] || OUTCOME_COLOR.unknown }}
          title={OUTCOME_LABEL[o] || o}
        />
      ))}
    </div>
  );
}

// ── Stat tile ──────────────────────────────────────────────────────────────

function Tile({ value, label, accent }) {
  return (
    <div className={`stat${accent ? ` stat-${accent}` : ""}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

// ── Drive card ─────────────────────────────────────────────────────────────

function DriveCard({ drive }) {
  const sr = drive.success_rate;
  const srAccent = sr == null
    ? null
    : sr >= 0.9 ? "good"
    : sr >= 0.7 ? "warn"
    : "error";

  return (
    <div className={`drive-health-card${drive.highlight ? " drive-health-highlight" : ""}`}>
      <div className="drive-health-card-header">
        {drive.highlight && <span className="drive-health-warn-icon" title="Success rate notably below fleet average">⚠</span>}
        <span className="drive-health-card-label">{drive.label}</span>
        <span className="drive-health-card-device">{drive.device_path}</span>
      </div>

      <div className="drive-health-tiles">
        <Tile value={drive.total_attempts} label="Attempts" />
        <Tile value={drive.clean}          label="Clean"    accent="good" />
        <Tile value={drive.dirty}          label="Dirty"    accent={drive.dirty > 0 ? "warn" : null} />
        <Tile value={drive.failed}         label="Failed"   accent={drive.failed > 0 ? "error" : null} />
        {drive.timed_out > 0 && (
          <Tile value={drive.timed_out} label="T/O†" accent="warn" />
        )}
        <Tile value={fmtPct(sr)} label="Success rate" accent={srAccent} />
        <Tile value={fmtDuration(drive.avg_clean_duration_seconds)} label="Avg clean time" />
        <Tile value="—" label="Avg read speed‡" />
      </div>

      <div className="drive-health-strip-row">
        <span className="drive-health-strip-label">Last {drive.last_10_outcomes.length}:</span>
        <OutcomeStrip outcomes={drive.last_10_outcomes} />
      </div>

      {drive.failed_here_ok_elsewhere > 0 && (
        <div className="drive-health-rescue">
          <span className="drive-health-rescue-count">{drive.failed_here_ok_elsewhere}</span>
          {" "}disc{drive.failed_here_ok_elsewhere !== 1 ? "s" : ""} failed here, ripped clean elsewhere
        </div>
      )}
    </div>
  );
}

// ── Cross-drive retry table ────────────────────────────────────────────────

function RetryTable({ retries }) {
  if (retries.length === 0) {
    return <p className="drive-health-no-data">No cross-drive retries found.</p>;
  }
  return (
    <table className="drive-health-retry-table">
      <thead>
        <tr>
          <th>Disc</th>
          <th>Failed on</th>
          <th>Failed</th>
          <th>Ripped clean on</th>
          <th>Succeeded</th>
        </tr>
      </thead>
      <tbody>
        {retries.map((r) => (
          <tr key={r.disc_id}>
            <td>{r.working_title}</td>
            <td>{r.failed_drive_label}</td>
            <td>{fmtDate(r.failed_at)}</td>
            <td>{r.success_drive_label}</td>
            <td>{fmtDate(r.success_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function DriveHealth() {
  const [env, setEnv] = useState("prod");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.getDriveHealth(env)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [env]);

  return (
    <div className="page-content">
      <div className="drive-health-header">
        <h2 className="page-title">Drive Health</h2>
        <div className="drive-health-env-toggle">
          <button
            className={`env-toggle-btn${env === "prod" ? " active" : ""}`}
            onClick={() => setEnv("prod")}
          >Prod</button>
          <button
            className={`env-toggle-btn${env === "dev" ? " active" : ""}`}
            onClick={() => setEnv("dev")}
          >Dev</button>
        </div>
      </div>

      {loading && <p className="drive-health-loading">Loading…</p>}
      {error   && <p className="drive-health-error">Error: {error}</p>}

      {data && !loading && (
        <>
          {data.drives.length === 0 ? (
            <p className="drive-health-no-data">No active drives found for {env}.</p>
          ) : (
            <div className="drive-health-cards">
              {data.drives.map((d) => <DriveCard key={d.drive_id} drive={d} />)}
            </div>
          )}

          <h3 className="drive-health-section-title">Cross-Drive Retries</h3>
          <p className="drive-health-section-desc">
            Discs that failed on one drive and were later ripped clean on another.
            A high count on the same failing drive is the strongest indicator of a hardware problem.
          </p>
          <RetryTable retries={data.cross_drive_retries} />

          <div className="drive-health-notes">
            <p>† Timed-out count is a heuristic: detected via <code>dvdbackup_failed</code> log events with
              "Timed out" in the detail text. If MakeMKV rescued the disc afterwards it still appears
              in Clean totals.</p>
            <p>‡ Average read speed (MB/s) cannot be computed: ISO file size is not stored in
              the database. To enable this metric, add <code>iso_size_bytes</code> to the <code>discs</code> table
              and populate it when the ISO build completes.</p>
          </div>
        </>
      )}
    </div>
  );
}
