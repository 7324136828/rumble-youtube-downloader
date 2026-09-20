const BASE = '/api';

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : Array.isArray(body.detail) ? body.detail.map((item) => item.msg || 'Invalid request').join('; ') : 'Invalid request';
    } catch {
      /* keep default detail */
    }
    throw new Error(detail);
  }
  return response.json();
}

export function createJob(urls, options, filename) {
  return request('/convert', {
    method: 'POST',
    body: JSON.stringify({ urls, options, filename }),
  });
}

export function getJobs() {
  return request('/jobs');
}

export function getJob(jobId) {
  return request(`/jobs/${jobId}`);
}

export async function getJobLogs(jobId) {
  const response = await fetch(`${BASE}/jobs/${jobId}/logs`);
  if (!response.ok) return '';
  return response.text();
}

export function discardJob(jobId) {
  return request(`/jobs/${jobId}/discard`, { method: 'POST' });
}

export function downloadZipUrl(jobId) {
  return `${BASE}/jobs/${jobId}/download-zip`;
}

export function getJobVideos(jobId) {
  return request(`/jobs/${jobId}/videos`);
}

export function getAllVideos() {
  return request('/videos');
}

export async function requestVideoStream(streamUrl) {
  const response = await fetch(streamUrl, { headers: { Range: 'bytes=0-0' } });
  return response.status;
}

export function getConnectors() {
  return request('/connectors');
}

export function resolveUrls(urls) {
  return request('/resolve', {
    method: 'POST',
    body: JSON.stringify({ urls }),
  });
}

export function startDownloads(urls, quality = 'best') {
  return request('/media', {
    method: 'POST',
    body: JSON.stringify({ urls, quality }),
  });
}

export function searchVideos(query, source = 'all', signal) {
  const params = new URLSearchParams({ q: query, source, limit: '12' });
  return request(`/search?${params}`, { signal });
}

export function getMediaItems(status) {
  return request(status ? `/media?status=${encodeURIComponent(status)}` : '/media');
}

export function getMediaItem(id) {
  return request(`/media/${id}`);
}

export function startMediaConversion(id, format) {
  return request(`/media/${id}/conversions/${format}`, { method: 'POST' });
}

export function getDownloadSettings(signal) {
  return request('/settings/downloads', { signal });
}

export function updateDownloadSettings(settings) {
  return request('/settings/downloads', {
    method: 'PATCH',
    body: JSON.stringify(settings),
  });
}

export function deleteMedia(id) {
  return request(`/media/${id}`, { method: 'DELETE' });
}

export function recordWatchHistory(videoId, { positionSeconds, watchedSeconds, completed = false }, { keepalive = false } = {}) {
  return request('/watch-history', {
    method: 'POST',
    keepalive,
    body: JSON.stringify({ video_id: videoId, position_seconds: positionSeconds, watched_seconds: watchedSeconds, completed }),
  });
}

export function getRecommendationSettings(signal) {
  return request('/recommendations/settings', { signal });
}

export function updateRecommendationSettings(settings) {
  return request('/recommendations/settings', {
    method: 'PATCH',
    body: JSON.stringify(settings),
  });
}

export function getRecommendationModels(signal) {
  return request('/recommendations/models', { signal });
}

export function importRecommendationConfig(name, config) {
  return request('/recommendations/configs', {
    method: 'POST',
    body: JSON.stringify({ name, config }),
  });
}

export function getRecommendations(options, signal) {
  return request('/recommendations', {
    method: 'POST',
    body: JSON.stringify(options),
    signal,
  });
}
