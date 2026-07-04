import React, { useCallback, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import { api } from "./api/client";

import DriveStatus from "./pages/DriveStatus.jsx";
import DvdEncoders from "./pages/DvdEncoders.jsx";
import CdEncoders from "./pages/CdEncoders.jsx";
import DbHealth from "./pages/DbHealth.jsx";
import DvdCatalogue from "./pages/DvdCatalogue.jsx";
import CdCatalogue from "./pages/CdCatalogue.jsx";
import DataEditing from "./pages/DataEditing.jsx";
import Log from "./pages/Log.jsx";
import Audit from "./pages/Audit.jsx";

const TABS = [
  { path: "/drive-status", label: "Drive Status" },
  { path: "/dvd-encoders", label: "DVD Encoders" },
  { path: "/cd-encoders", label: "CD Encoders" },
  { path: "/dvd-catalogue", label: "DVD Catalogue" },
  { path: "/cd-catalogue", label: "CD Catalogue" },
  { path: "/data-editing", label: "Identification" },
  { path: "/db-health", label: "Health" },
  { path: "/audit", label: "Audit" },
  { path: "/log", label: "Log" },
];

const THEME_STORAGE_KEY = "discripper-theme";
const HEARTBEAT_STALE_THRESHOLD_MS = 60000;

function loadStoredTheme() {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "dark" || stored === "light" ? stored : "dark";
}

function NavServiceStatus({ label, serviceStatus, serviceHeartbeat, onStop }) {
  const [now, setNow] = useState(Date.now());
  const [stopRequested, setStopRequested] = useState(false);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const heartbeatMs = serviceHeartbeat ? new Date(serviceHeartbeat).getTime() : null;
  const isStale = heartbeatMs === null || now - heartbeatMs > HEARTBEAT_STALE_THRESHOLD_MS;

  useEffect(() => {
    if (serviceStatus === "stopped" || (serviceStatus === "running" && isStale)) {
      setStopRequested(false);
    }
  }, [serviceStatus, isStale]);

  let pillClass, pillText;
  if (serviceStatus === "running" && !isStale) {
    pillClass = "good";
    pillText = "Running";
  } else if (serviceStatus === "running" && isStale) {
    pillClass = "warn";
    pillText = "Not Responding";
  } else {
    pillClass = "error";
    pillText = "Stopped";
  }

  const isStopped = serviceStatus === "stopped";

  function handleStop() {
    setStopRequested(true);
    onStop();
  }

  return (
    <div className="nav-service-control">
      <span className="nav-service-label">{label}</span>
      <span className={`status-pill ${pillClass}`}>{pillText}</span>
      <button
        className="nav-service-btn"
        onClick={handleStop}
        disabled={stopRequested || isStopped}
        title={isStopped ? `${label} service is stopped` : `Request a clean shutdown of the ${label.toLowerCase()} service`}
      >
        {stopRequested && !isStopped ? "Stopping…" : "Stop"}
      </button>
    </div>
  );
}

export default function App() {
  const [env, setEnv] = useState(null);
  const [theme, setTheme] = useState(loadStoredTheme);
  const [fakeRipMode, setFakeRipMode] = useState(false);
  const [savingFakeRipMode, setSavingFakeRipMode] = useState(false);
  const [fakeDirtyMode, setFakeDirtyMode] = useState(false);
  const [savingFakeDirtyMode, setSavingFakeDirtyMode] = useState(false);
  const [serviceStatus, setServiceStatus] = useState("stopped");
  const [serviceHeartbeat, setServiceHeartbeat] = useState(null);
  const [encoderServiceStatus, setEncoderServiceStatus] = useState("stopped");
  const [encoderServiceHeartbeat, setEncoderServiceHeartbeat] = useState(null);

  useEffect(() => {
    api.ping()
      .then((data) => setEnv(data.environment))
      .catch(() => setEnv("unreachable"));
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    if (env !== "dev") return;
    api.getFakeRipMode()
      .then((data) => setFakeRipMode(data.fake_rip_mode))
      .catch(() => {});
    api.getFakeDirtyMode()
      .then((data) => setFakeDirtyMode(data.fake_dirty_mode))
      .catch(() => {});
  }, [env]);

  const fetchServiceStatus = useCallback(() => {
    api.getServiceStatus()
      .then((data) => setServiceStatus(data.service_status))
      .catch(() => {});
  }, []);

  const fetchServiceHeartbeat = useCallback(() => {
    api.getServiceHeartbeat()
      .then((data) => setServiceHeartbeat(data.service_heartbeat))
      .catch(() => {});
  }, []);

  const fetchEncoderServiceStatus = useCallback(() => {
    api.getEncoderServiceStatus()
      .then((data) => setEncoderServiceStatus(data.encoder_service_status))
      .catch(() => {});
  }, []);

  const fetchEncoderServiceHeartbeat = useCallback(() => {
    api.getEncoderServiceHeartbeat()
      .then((data) => setEncoderServiceHeartbeat(data.encoder_service_heartbeat))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchServiceStatus();
    fetchServiceHeartbeat();
    fetchEncoderServiceStatus();
    fetchEncoderServiceHeartbeat();
    const t = setInterval(() => {
      fetchServiceStatus();
      fetchServiceHeartbeat();
      fetchEncoderServiceStatus();
      fetchEncoderServiceHeartbeat();
    }, 1000);
    return () => clearInterval(t);
  }, [fetchServiceStatus, fetchServiceHeartbeat, fetchEncoderServiceStatus, fetchEncoderServiceHeartbeat]);

  async function handleStopService() {
    await api.setServiceCommand("exit");
  }

  async function handleStopEncoderService() {
    await api.setEncoderServiceCommand("exit");
  }

  async function toggleFakeRipMode() {
    setSavingFakeRipMode(true);
    try {
      const data = await api.setFakeRipMode(!fakeRipMode);
      setFakeRipMode(data.fake_rip_mode);
    } finally {
      setSavingFakeRipMode(false);
    }
  }

  async function toggleFakeDirtyMode() {
    setSavingFakeDirtyMode(true);
    try {
      const data = await api.setFakeDirtyMode(!fakeDirtyMode);
      setFakeDirtyMode(data.fake_dirty_mode);
    } finally {
      setSavingFakeDirtyMode(false);
    }
  }

  return (
    <BrowserRouter>
      <div className="app">
        <header className="app-header">
          <h1>Disc Ripper</h1>
          <div className="header-controls">
            {env === "dev" && (
              <button
                className={`fake-rip-toggle${fakeRipMode ? " active" : ""}`}
                onClick={toggleFakeRipMode}
                disabled={savingFakeRipMode}
                title="Fake rip mode: uses a fake dvdbackup stand-in instead of real hardware (dev only)"
              >
                Fake Mode
              </button>
            )}
            {env === "dev" && (
              <button
                className={`fake-dirty-toggle${fakeDirtyMode ? " active" : ""}`}
                onClick={toggleFakeDirtyMode}
                disabled={savingFakeDirtyMode || !fakeRipMode}
                title={
                  fakeRipMode
                    ? "Fake dirty mode: when fake-ripping on Drive 1, simulates a recoverable read error to test dirty-rip detection (dev only)"
                    : "Requires Fake Mode to be on - dirty simulation only takes effect within fake rip runs"
                }
              >
                Dirty Mode
              </button>
            )}
            <button
              className="theme-toggle"
              onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            >
              {theme === "dark" ? "☀ Light" : "☾ Dark"}
            </button>
            {env && (
              <span className={`env-badge ${env}`}>
                {env === "unreachable" ? "API unreachable" : env}
              </span>
            )}
          </div>
        </header>

        <nav className="tabs">
          <div className="tabs-links">
            {TABS.map((tab) => (
              <NavLink
                key={tab.path}
                to={tab.path}
                className={({ isActive }) => `tab-link${isActive ? " active" : ""}`}
              >
                {tab.label}
              </NavLink>
            ))}
          </div>
          <div className="nav-services">
            <NavServiceStatus
              label="Ripper"
              serviceStatus={serviceStatus}
              serviceHeartbeat={serviceHeartbeat}
              onStop={handleStopService}
            />
            <NavServiceStatus
              label="Encoder"
              serviceStatus={encoderServiceStatus}
              serviceHeartbeat={encoderServiceHeartbeat}
              onStop={handleStopEncoderService}
            />
          </div>
        </nav>

        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/drive-status" replace />} />
            <Route path="/drive-status" element={<DriveStatus />} />
            <Route path="/dvd-encoders" element={<DvdEncoders />} />
            <Route path="/cd-encoders" element={<CdEncoders />} />
            <Route path="/db-health" element={<DbHealth />} />
            <Route path="/dvd-catalogue" element={<DvdCatalogue />} />
            <Route path="/cd-catalogue" element={<CdCatalogue />} />
            <Route path="/data-editing" element={<DataEditing />} />
            <Route path="/log" element={<Log />} />
            <Route path="/audit" element={<Audit />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
