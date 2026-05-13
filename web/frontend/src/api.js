const BASE = '/api';

async function apiFetch(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function getHealth() {
  return apiFetch('/health');
}

export async function getClips({ vehicle, event_type, folder, camera, date_from, date_to } = {}) {
  const params = new URLSearchParams();
  if (vehicle) params.set('vehicle', vehicle);
  if (event_type) params.set('event_type', event_type);
  if (folder) params.set('folder', folder);
  if (camera) params.set('camera', camera);
  if (date_from) params.set('date_from', date_from);
  if (date_to) params.set('date_to', date_to);
  const qs = params.toString();
  return apiFetch(`/clips${qs ? '?' + qs : ''}`);
}

export async function triggerScan() {
  return apiFetch('/scan');
}

export async function clearCache() {
  return apiFetch('/cache/clear');
}