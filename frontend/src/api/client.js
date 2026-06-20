const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`API error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

export const api = {
  ping: () => request("/ping"),
  health: () => request("/health/"),
  discs: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/discs/${qs ? `?${qs}` : ""}`);
  },
  disc: (id) => request(`/discs/${id}`),
  drives: () => request("/drives/"),
  encodeProfiles: () => request("/encode-profiles/"),
  saveTempName: (discId, name) =>
    request(`/discs/${discId}/temp-name`, {
      method: "PATCH",
      body: JSON.stringify({ temp_name: name }),
    }),
  ejectDisc: (discId) =>
    request(`/discs/${discId}/eject`, { method: "POST" }),
  getMaxRippers: () => request("/settings/max-rippers"),
  setMaxRippers: (n) =>
    request("/settings/max-rippers", {
      method: "PUT",
      body: JSON.stringify({ max_rippers: n }),
    }),
  startRegionRead: (driveId) =>
    request(`/drives/${driveId}/region/start-read`, { method: "POST" }),
  rereadRegion: (driveId) =>
    request(`/drives/${driveId}/region/reread`, { method: "POST" }),
  ejectDriveDirectly: (driveId) =>
    request(`/drives/${driveId}/eject`, { method: "POST" }),
  getFakeRipMode: () => request("/settings/fake-rip-mode"),
  setFakeRipMode: (enabled) =>
    request("/settings/fake-rip-mode", {
      method: "PUT",
      body: JSON.stringify({ fake_rip_mode: enabled }),
    }),
  getRippingEnabled: () => request("/settings/ripping-enabled"),
  setRippingEnabled: (enabled) =>
    request("/settings/ripping-enabled", {
      method: "PUT",
      body: JSON.stringify({ ripping_enabled: enabled }),
    }),
};
