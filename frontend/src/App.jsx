import React, { useEffect, useState } from "react";
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

function loadStoredTheme() {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  return stored === "dark" || stored === "light" ? stored : "dark";
}

export default function App() {
  const [env, setEnv] = useState(null);
  const [theme, setTheme] = useState(loadStoredTheme);
  const [fakeRipMode, setFakeRipMode] = useState(false);
  const [savingFakeRipMode, setSavingFakeRipMode] = useState(false);
  const [fakeDirtyMode, setFakeDirtyMode] = useState(false);
  const [savingFakeDirtyMode, setSavingFakeDirtyMode] = useState(false);

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
          {TABS.map((tab) => (
            <NavLink
              key={tab.path}
              to={tab.path}
              className={({ isActive }) => `tab-link${isActive ? " active" : ""}`}
            >
              {tab.label}
            </NavLink>
          ))}
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
