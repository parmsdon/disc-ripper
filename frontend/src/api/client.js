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
  getFakeDirtyMode: () => request("/settings/fake-dirty-mode"),
  setFakeDirtyMode: (enabled) =>
    request("/settings/fake-dirty-mode", {
      method: "PUT",
      body: JSON.stringify({ fake_dirty_mode: enabled }),
    }),
  getRippingEnabled: () => request("/settings/ripping-enabled"),
  setRippingEnabled: (enabled) =>
    request("/settings/ripping-enabled", {
      method: "PUT",
      body: JSON.stringify({ ripping_enabled: enabled }),
    }),
  getServiceStatus: () => request("/settings/service-status"),
  getServiceHeartbeat: () => request("/settings/service-heartbeat"),
  setServiceCommand: (command) =>
    request("/settings/service-command", {
      method: "PUT",
      body: JSON.stringify({ service_command: command }),
    }),
  getCatalog: (search) =>
    request(`/catalog/${search ? `?search=${encodeURIComponent(search)}` : ""}`),
  triggerSync: () => request("/catalog/sync", { method: "POST" }),
  getSyncStatus: () => request("/catalog/sync/status"),
  getDiscCandidates: (discId) => request(`/discs/${discId}/candidates`),
  getIdentificationQueue: () => request("/discs/identification-queue"),
  identifyDvd: (discId, catalogId) =>
    request(`/discs/${discId}/identify-dvd`, {
      method: "PATCH",
      body: JSON.stringify({ catalog_id: catalogId }),
    }),
  identifyCd: (discId, data) =>
    request(`/discs/${discId}/identify-cd`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  getCatalogSuggestions: (title) =>
    request(`/catalog/unmatched-suggestions?title=${encodeURIComponent(title)}&limit=3`),
  searchCatalog: (query) =>
    request(`/catalog/?search=${encodeURIComponent(query)}&exclude_matched=true`),
  getLog: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/log/${qs ? `?${qs}` : ""}`);
  },
  getDvdCatalogue: ({ ripStatus, idStatus, mmStatus, search, dirty } = {}) => {
    const p = new URLSearchParams();
    if (ripStatus) p.set("rip_status", ripStatus);
    if (idStatus) p.set("id_status", idStatus);
    if (mmStatus) p.set("mm_status", mmStatus);
    if (search) p.set("search", search);
    if (dirty) p.set("dirty", "true");
    const qs = p.toString();
    return request(`/catalog/dvd-catalogue${qs ? `?${qs}` : ""}`);
  },
  getCdCatalogue: (filter, search, dirty) =>
    request(`/discs/cd-catalogue?filter=${filter || "all"}${search ? `&search=${encodeURIComponent(search)}` : ""}${dirty ? "&dirty=true" : ""}`),
  deleteDisc: (discId) => request(`/discs/${discId}`, { method: "DELETE" }),
  retryRip: (discId) => request(`/discs/${discId}/retry-rip`, { method: "POST" }),
  cancelRip: (discId) => request(`/discs/${discId}/cancel-rip`, { method: "POST" }),
  getOldIsos: () => request("/discs/old-isos"),
  reconcileDisc: (data) => request("/discs/reconcile", { method: "POST", body: JSON.stringify(data) }),
  getMbNotFound: () => request("/health/mb-not-found"),
  getMbError: () => request("/health/mb-error"),
  getPipelineIdentifying: () => request("/health/pipeline-identifying"),
  getPipelineErrors: () => request("/health/pipeline-errors"),
  getDvdsProtected: () => request("/health/dvds-protected"),
  getLibraryStatus: () => request("/library/status"),
  generateLibrary: () => request("/library/generate", { method: "POST" }),
  getAudit: () => request("/audit/"),
  createMissingDvdEncodeJobs: () => request("/audit/create-missing-dvd-encode-jobs", { method: "POST" }),
  createMissingCdEncodeJobs: () => request("/audit/create-missing-cd-encode-jobs", { method: "POST" }),
  fixStaleDvdDriveAssociations: () => request("/audit/fix-stale-dvd-drive-associations", { method: "POST" }),
  fixStaleCdDriveAssociations: () => request("/audit/fix-stale-cd-drive-associations", { method: "POST" }),
  cleanupOrphanedWavDirs: () => request("/audit/cleanup-orphaned-wav-dirs", { method: "POST" }),
  checkTempName: (name, discId, type) =>
    request(`/discs/check-temp-name?name=${encodeURIComponent(name)}&disc_id=${discId}&type=${type}`),

  // Encode jobs
  getEncodeJobs: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/encode/jobs${qs ? `?${qs}` : ""}`);
  },
  getEncodeStats: () => request("/encode/stats"),
  getEncodeProfiles: () => request("/encode/profiles"),

  // Encoder settings
  getDvdEncodingEnabled: () => request("/settings/dvd-encoding-enabled"),
  setDvdEncodingEnabled: (enabled) =>
    request("/settings/dvd-encoding-enabled", {
      method: "PUT",
      body: JSON.stringify({ dvd_encoding_enabled: enabled }),
    }),
  getCdEncodingEnabled: () => request("/settings/cd-encoding-enabled"),
  setCdEncodingEnabled: (enabled) =>
    request("/settings/cd-encoding-enabled", {
      method: "PUT",
      body: JSON.stringify({ cd_encoding_enabled: enabled }),
    }),
  getMaxDvdEncoders: () => request("/settings/max-dvd-encoders"),
  setMaxDvdEncoders: (n) =>
    request("/settings/max-dvd-encoders", {
      method: "PUT",
      body: JSON.stringify({ max_dvd_encoders: n }),
    }),
  getMaxCdEncoders: () => request("/settings/max-cd-encoders"),
  setMaxCdEncoders: (n) =>
    request("/settings/max-cd-encoders", {
      method: "PUT",
      body: JSON.stringify({ max_cd_encoders: n }),
    }),

  // Encoder service status (written by encoder_service, read-only here except command)
  getEncoderServiceStatus: () => request("/settings/encoder-service-status"),
  getEncoderServiceHeartbeat: () => request("/settings/encoder-service-heartbeat"),
  setEncoderServiceCommand: (command) =>
    request("/settings/encoder-service-command", {
      method: "PUT",
      body: JSON.stringify({ encoder_service_command: command }),
    }),
};
