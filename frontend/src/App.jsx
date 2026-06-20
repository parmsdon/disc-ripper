import React, { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import { api } from "./api/client";

import DriveStatus from "./pages/DriveStatus.jsx";
import DvdEncoders from "./pages/DvdEncoders.jsx";
import CdEncoders from "./pages/CdEncoders.jsx";
import DbHealth from "./pages/DbHealth.jsx";
import DataEditing from "./pages/DataEditing.jsx";

const TABS = [
  { path: "/drive-status", label: "Drive Status" },
  { path: "/dvd-encoders", label: "DVD Encoders" },
  { path: "/cd-encoders", label: "CD Encoders" },
  { path: "/db-health", label: "DB Health" },
  { path: "/data-editing", label: "Data Editing" },
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
  const [rippingEnabled, setRippingEnabled] = useState(false);
  const [savingRippingEnabled, setSavingRippingEnabled] = useState(false);

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
  }, [env]);

  useEffect(() => {
    if (!env || env === "unreachable") return;
    api.getRippingEnabled()
      .then((data) => setRippingEnabled(data.ripping_enabled))
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

  async function toggleRippingEnabled() {
    setSavingRippingEnabled(true);
    try {
      const data = await api.setRippingEnabled(!rippingEnabled);
      setRippingEnabled(data.ripping_enabled);
    } finally {
      setSavingRippingEnabled(false);
    }
  }

  return (
    <BrowserRouter>
      <div className="app">
        <header className="app-header">
          <h1>Disc Ripper</h1>
          <div className="header-controls">
            {env && env !== "unreachable" && (
              <button
                className={`ripping-toggle${rippingEnabled ? " active" : ""}`}
                onClick={toggleRippingEnabled}
                disabled={savingRippingEnabled}
                title={
                  rippingEnabled
                    ? "Ripping is enabled - click to stop"
                    : "Ripping is stopped - click to start"
                }
              >
                {rippingEnabled ? "Stop Ripping" : "Start Ripping"}
              </button>
            )}
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
            <Route path="/data-editing" element={<DataEditing />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
