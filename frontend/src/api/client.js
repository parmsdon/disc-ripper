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
};
