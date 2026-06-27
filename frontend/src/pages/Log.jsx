import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";

const REFRESH_MS = 5000;

const EVENT_LABELS = {
  disc_inserted:   "Disc Inserted",
  disc_ejected:    "Disc Ejected",
  rip_started:     "Rip Started",
  rip_completed:   "Rip Completed",
  rip_failed:      "Rip Failed",
  track_started:   "Track Started",
  track_completed: "Track Completed",
};

const ALL_EVENT_TYPES = Object.keys(EVENT_LABELS);

function formatTime(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (isToday) return time;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " + time;
}

function OutcomeBadge({ outcome }) {
  if (!outcome) return <span className="catalogue-dim">—</span>;
  const cls = ["clean", "good"].includes(outcome) ? "done"
    : ["dirty", "imperfect"].includes(outcome) ? "imperfect"
    : ["error", "failed"].includes(outcome) ? "error"
    : null;
  return <span className={`status-pill${cls ? ` ${cls}` : ""}`}>{outcome}</span>;
}

export default function Log() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [drive, setDrive] = useState("");
  const [eventType, setEventType] = useState("");
  const [limit, setLimit] = useState(100);

  const fetch = useCallback(() => {
    const params = { limit };
    if (drive) params.drive = drive;
    if (eventType) params.event_type = eventType;
    api.getLog(params)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message));
  }, [drive, eventType, limit]);

  useEffect(() => {
    fetch();
    const id = setInterval(fetch, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetch]);

  const driveLabels = data?.drive_labels ?? [];
  const events = data?.events ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="panel">
      <h2>Ripping Activity Log</h2>

      <div className="log-filters">
        <select
          className="log-filter-select"
          value={drive}
          onChange={(e) => setDrive(e.target.value)}
        >
          <option value="">All Drives</option>
          {driveLabels.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>

        <select
          className="log-filter-select"
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
        >
          <option value="">All Events</option>
          {ALL_EVENT_TYPES.map((t) => (
            <option key={t} value={t}>{EVENT_LABELS[t]}</option>
          ))}
        </select>

        <div style={{ display: "flex", gap: 4 }}>
          {[100, 500].map((n) => (
            <button
              key={n}
              className={`log-limit-btn${limit === n ? " active" : ""}`}
              onClick={() => setLimit(n)}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="log-empty">Error: {error}</div>}

      {!error && (
        <>
          <div className="log-count">
            {data === null
              ? "Loading…"
              : `Showing ${events.length} of ${total} event${total !== 1 ? "s" : ""}`}
          </div>

          {data !== null && events.length === 0 ? (
            <div className="log-empty">No ripping activity logged yet.</div>
          ) : (
            <table className="log-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Drive</th>
                  <th>Working Title</th>
                  <th>Track #</th>
                  <th>Event</th>
                  <th>Outcome</th>
                  <th>Elapsed</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} className="log-row">
                    <td className="log-time">{formatTime(e.occurred_at)}</td>
                    <td>{e.drive_label ?? <span className="catalogue-dim">—</span>}</td>
                    <td>
                      {e.working_title
                        ? <span className="log-title" title={e.working_title}>{e.working_title}</span>
                        : <span className="catalogue-dim">—</span>}
                    </td>
                    <td>{e.track_number ?? <span className="catalogue-dim">—</span>}</td>
                    <td>{EVENT_LABELS[e.event_type] ?? e.event_type}</td>
                    <td><OutcomeBadge outcome={e.outcome} /></td>
                    <td>{e.elapsed_display ?? <span className="catalogue-dim">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
