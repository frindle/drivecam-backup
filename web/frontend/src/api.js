const BASE = '/api';

async function apiFetch(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function getHealth() {
  return apiFetch('/health');
}

export async function getClips({ vehicle, event_type, folder, camera, date_from, date_to, before_timestamp, limit } = {}) {
  const params = new URLSearchParams();
  if (vehicle) params.set('vehicle', vehicle);
  if (event_type) params.set('event_type', event_type);
  if (folder) params.set('folder', folder);
  if (camera) params.set('camera', camera);
  if (date_from) params.set('date_from', date_from);
  if (date_to) params.set('date_to', date_to);
  if (before_timestamp) params.set('before_timestamp', before_timestamp);
  if (limit != null) params.set('limit', String(limit));
  const qs = params.toString();
  return apiFetch(`/clips${qs ? '?' + qs : ''}`);
}

export async function getEvents({ vehicle, event_type, folder, camera, date_from, date_to, before_timestamp, limit } = {}) {
  const params = new URLSearchParams();
  if (vehicle) params.set('vehicle', vehicle);
  if (event_type) params.set('event_type', event_type);
  if (folder) params.set('folder', folder);
  if (camera) params.set('camera', camera);
  if (date_from) params.set('date_from', date_from);
  if (date_to) params.set('date_to', date_to);
  if (before_timestamp) params.set('before_timestamp', before_timestamp);
  if (limit != null) params.set('limit', String(limit));
  const qs = params.toString();
  return apiFetch(`/events${qs ? '?' + qs : ''}`);
}

export async function triggerScan() {
  return apiFetch('/scan');
}

export async function clearCache() {
  return apiFetch('/cache/clear');
}

export async function getShares() {
  const res = await fetch(`${BASE}/shares`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function createShare(share) {
  const res = await fetch(`${BASE}/shares`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(share),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function updateShare(shareId, share) {
  const res = await fetch(`${BASE}/shares/${shareId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(share),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function deleteShare(shareId) {
  const res = await fetch(`${BASE}/shares/${shareId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function testShare(shareId) {
  const res = await fetch(`${BASE}/shares/${shareId}/test`, { method: 'POST' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function getScanStatus() {
  const res = await fetch(`${BASE}/scan/status`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function getClipDuration(path) {
  const encoded = encodeURIComponent(path);
  return apiFetch(`/clips/${encoded}/duration`);
}

export async function getCloudProviders() {
  return apiFetch('/cloud/providers');
}

export async function createCloudProvider(provider) {
  const res = await fetch(`${BASE}/cloud/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(provider),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function updateCloudProvider(providerId, provider) {
  const res = await fetch(`${BASE}/cloud/providers/${providerId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(provider),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function deleteCloudProvider(providerId) {
  const res = await fetch(`${BASE}/cloud/providers/${providerId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function testCloudProvider(providerId) {
  const res = await fetch(`${BASE}/cloud/providers/${providerId}/test`, { method: 'POST' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function syncCloudProvider(providerId) {
  const res = await fetch(`${BASE}/cloud/providers/${providerId}/sync`, { method: 'POST' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function getCloudOAuthUrl(provider) {
  return apiFetch(`/cloud/oauth/${provider}/url`);
}

export async function uploadFiles(formData) {
  const res = await fetch(`${BASE}/upload`, { method: 'POST', body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Upload failed');
  }
  return res.json();
}

export async function getStorageStats() {
  return apiFetch('/stats/storage');
}

export async function getVehicleFolders() {
  return apiFetch('/vehicles/folders');
}