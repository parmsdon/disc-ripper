import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

const STATUS_FILTERS = ["all", "running", "queued", "done", "error"];

function EncoderToggle({ enabled, saving, onToggle }) {
  return (
    <button
      className={`ripping-toggle${enabled ? " active" : ""}`}
      onClick={onToggle}
      disabled={saving}
      title={enabled ? "DVD encoding enabled — click to disable" : "DVD encoding disabled — click to enable"}
    >
      {enabled ? "Encoding On" : "Encoding Off"}
    </button>
  );
}

function MaxEncodersControl({ value, saving, onChange }) {
  function update(next) {
    const clamped = Math.max(1, next);
    if (clamped === value) return;
    onChange(clamped);
  }
  return (
    <div className="control-bar-group">
      <span className="control-bar-label">Max DVD encoders:</span>
      <button onClick={() => update(value - 1)} disabled={saving || value <= 1}>−</button>
      <input
        type="number"
        min="1"
        className="max-rippers-input"
        value={value}
        disabled={saving}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          if (!Number.isNaN(n)) update(n);
        }}
      />
      <button onClick={() => update(value + 1)} disabled={saving}>+</button>
    </div>
  );
}

function StatsPills({ stats }) {
  if (!stats) return null;
  const s = stats.dvd;
  return (
    <div className="encoder-stats">
      {s.running > 0 && <span className="encoder-stat-pill running">{s.running} running</span>}
      {s.queued > 0  && <span className="encoder-stat-pill queued">{s.queued} queued</span>}
      {s.done > 0    && <span className="encoder-stat-pill done">{s.done} done</span>}
      {s.error > 0   && <span className="encoder-stat-pill error">{s.error} error{s.error !== 1 ? "s" : ""}</span>}
      {s.running === 0 && s.queued === 0 && s.done === 0 && s.error === 0 && (
        <span className="encoder-stat-pill idle">No jobs</span>
      )}
    </div>
  );
}

function EncodeProgressCell({ job }) {
  if (job.status === "running") {
    const pct = job.progress_percent ?? 0;
    return (
      <div className="encoder-progress">
        <div className="progress-bar-track">
          <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="progress-bar-label">{pct}%</span>
      </div>
    );
  }
  if (job.status === "done") return <span className="encoder-done-pct">100%</span>;
  if (job.status === "error") {
    return (
      <span className="error-hint" title={job.error_message}>
        {job.error_message ? job.error_message.slice(0, 60) + (job.error_message.length > 60 ? "…" : "") : "Error"}
      </span>
    );
  }
  return <span className="text-dim">—</span>;
}

function formatTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso + "Z");
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDuration(job) {
  const start = job.started_at ? new Date(job.started_at + "Z") : null;
  const end = job.completed_at ? new Date(job.completed_at + "Z") : null;
  if (!start) return "—";
  const ms = (end || new Date()) - start;
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

function JobsTable({ jobs, mediaType }) {
  if (jobs.length === 0) {
    return <div className="empty-state">No DVD encode jobs yet</div>;
  }
  return (
    <table className="catalogue-table encoder-jobs-table">
      <thead>
        <tr>
          <th>Disc</th>
          <th>Profile</th>
          <th>Status</th>
          <th>Progress</th>
          <th>Started</th>
          <th>Duration</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.id} className="catalogue-row encoder-job-row">
            <td>
              <span className="encoder-disc-name">
                {job.disc_temp_name || <span className="text-dim">Disc #{job.disc_id}</span>}
              </span>
            </td>
            <td>
              <span className="encoder-profile-name">{job.profile_name}</span>
            </td>
            <td>
              <span className={`status-pill ${job.status}`}>{job.status}</span>
            </td>
            <td>
              <EncodeProgressCell job={job} />
            </td>
            <td className="encoder-time">{formatTime(job.started_at)}</td>
            <td className="encoder-duration">{formatDuration(job)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function DvdEncoders() {
  const [jobs, setJobs] = useState(null);
  const [stats, setStats] = useState(null);
  const [encodingEnabled, setEncodingEnabled] = useState(false);
  const [maxEncoders, setMaxEncoders] = useState(1);
  const [savingEnabled, setSavingEnabled] = useState(false);
  const [savingMax, setSavingMax] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");

  const fetchJobs = useCallback(() => {
    const params = { media_type: "dvd" };
    if (statusFilter !== "all") params.status = statusFilter;
    api.getEncodeJobs(params)
      .then(setJobs)
      .catch(() => setJobs([]));
  }, [statusFilter]);

  const fetchStats = useCallback(() => {
    api.getEncodeStats().then(setStats).catch(() => {});
  }, []);

  useEffect(() => {
    api.getDvdEncodingEnabled()
      .then((d) => setEncodingEnabled(d.dvd_encoding_enabled))
      .catch(() => {});
    api.getMaxDvdEncoders()
      .then((d) => setMaxEncoders(d.max_dvd_encoders))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchJobs();
    fetchStats();
    const iv = setInterval(() => { fetchJobs(); fetchStats(); }, 5000);
    return () => clearInterval(iv);
  }, [fetchJobs, fetchStats]);

  async function toggleEnabled() {
    setSavingEnabled(true);
    try {
      const data = await api.setDvdEncodingEnabled(!encodingEnabled);
      setEncodingEnabled(data.dvd_encoding_enabled);
    } finally {
      setSavingEnabled(false);
    }
  }

  async function changeMax(n) {
    setSavingMax(true);
    try {
      const data = await api.setMaxDvdEncoders(n);
      setMaxEncoders(data.max_dvd_encoders);
    } finally {
      setSavingMax(false);
    }
  }

  return (
    <div>
      <div className="control-bar encoder-header">
        <div className="control-bar-group">
          <span className="control-bar-label">DVD Encoding</span>
          <EncoderToggle enabled={encodingEnabled} saving={savingEnabled} onToggle={toggleEnabled} />
        </div>
        <MaxEncodersControl value={maxEncoders} saving={savingMax} onChange={changeMax} />
        <StatsPills stats={stats} />
      </div>

      <div className="panel">
        <div className="encoder-filter-row">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f}
              className={`catalogue-filter-btn${statusFilter === f ? " active" : ""}`}
              onClick={() => setStatusFilter(f)}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>

        {jobs === null ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <JobsTable jobs={jobs} mediaType="dvd" />
        )}
      </div>
    </div>
  );
}
