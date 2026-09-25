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

export function searchVideos(query, source = 'all', signal, limit = 12, session = '', fetchAll = false) {
  const params = new URLSearchParams({ q: query, source, limit: String(limit) });
  if (session) params.set('session', session);
  if (fetchAll) params.set('fetch_all', 'true');
  return request(`/search?${params}`, { signal });
}

export function getMediaItems(status) {
  return request(status ? `/media?status=${encodeURIComponent(status)}` : '/media');
}

export function getMediaItem(id) {
  return request(`/media/${id}`);
}

export function getVideoKeywords(query = '', signal, session = '') {
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (query && session) params.set('session', session);
  const suffix = params.toString();
  return request(`/video-keywords${suffix ? `?${suffix}` : ''}`, { signal });
}

export function updateMediaRetention(id, retentionDays, signal) {
  return request(`/media/${encodeURIComponent(id)}/retention`, {
    method: 'PATCH', body: JSON.stringify({ retention_days: retentionDays }), signal,
  });
}

export function startMediaConversion(id, format) {
  return request(`/media/${id}/conversions/${format}`, { method: 'POST' });
}

export function updateMediaPlayback(id, format) {
  return request(`/media/${encodeURIComponent(id)}/playback`, {
    method: 'PATCH', body: JSON.stringify({ format }),
  });
}

export function uploadMedia(file, thumbnail, title = '') {
  const body = new FormData();
  body.append('file', file);
  if (thumbnail) body.append('thumbnail', thumbnail);
  if (title.trim()) body.append('title', title.trim());
  return request('/media/upload', { method: 'POST', headers: {}, body });
}

export function uploadMediaThumbnail(id, thumbnail) {
  const body = new FormData();
  body.append('thumbnail', thumbnail);
  return request(`/media/${encodeURIComponent(id)}/thumbnail`, { method: 'POST', headers: {}, body });
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

export function getLinkSettings({ state = 'all', limit = 200, offset = 0 } = {}, signal) {
  return request(`/settings/links?${new URLSearchParams({ state, limit: String(limit), offset: String(offset) })}`, { signal });
}

export function updateLinkSettings(settings) {
  return request('/settings/links', { method: 'PATCH', body: JSON.stringify(settings) });
}

export function updateLinkState(linkId, state) {
  return request(`/settings/links/${encodeURIComponent(linkId)}`, {
    method: 'PATCH', body: JSON.stringify({ state }),
  });
}

export function deleteRecordedLink(linkId) {
  return request(`/settings/links/${encodeURIComponent(linkId)}`, { method: 'DELETE' });
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

export function getWatchHistory(signal) {
  return request('/watch-history?limit=100', { signal });
}

export function getRecommendationSettings(signal) {
  return request('/recommendations/settings', { signal });
}

export function getConnectorStatus(signal) {
  return request('/connector/status', { signal });
}

export function updateConnectorSettings(settings) {
  return request('/connector/settings', { method: 'PATCH', body: JSON.stringify(settings) });
}

export function connectDownloader() {
  return request('/connector/connect', { method: 'POST' });
}

export function recordRecommendationImpressions(impressions, signal) {
  return request('/connector/impressions', { method: 'POST', body: JSON.stringify(impressions), signal });
}

export function updateRecommendationSettings(settings) {
  return request('/recommendations/settings', {
    method: 'PATCH',
    body: JSON.stringify(settings),
  });
}

export function detectRecommendationThumbnailDomains(providerId, signal) {
  return request(`/recommendations/providers/${encodeURIComponent(providerId)}/thumbnail-domains/detect`, {
    method: 'POST', signal,
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

export function getWatchLater({ source = 'all', limit = 200, offset = 0 } = {}, signal) {
  return request(`/recommendations/watch-later?${new URLSearchParams({ source, limit: String(limit), offset: String(offset) })}`, { signal });
}

export function saveWatchLater(videos, { fetchTitles = true, resolveRedirects = true } = {}) {
  return request('/recommendations/watch-later', { method: 'POST', body: JSON.stringify({ videos, fetch_titles: fetchTitles, resolve_redirects: resolveRedirects }) });
}

export function fetchWatchLaterTitle(catalogId, signal, { force = false } = {}) {
  const query = force ? '?force=true' : '';
  return request(`/recommendations/watch-later/${encodeURIComponent(catalogId)}/title${query}`, { method: 'POST', signal });
}

export function removeWatchLater(catalogId) {
  return request(`/recommendations/watch-later/${encodeURIComponent(catalogId)}`, { method: 'DELETE' });
}

export function getWatchLaterLinks({ limit = 200, offset = 0 } = {}, signal) {
  return request(`/recommendations/watch-later/links?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`, { signal });
}

export function saveAllPageLinks(pageUrl) {
  return request('/recommendations/watch-later/save-all-links', { method: 'POST', body: JSON.stringify({ page_url: pageUrl }) });
}

export function previewPageLinks(pageUrl) {
  return request('/recommendations/watch-later/preview-links', { method: 'POST', body: JSON.stringify({ page_url: pageUrl }) });
}

export function removeWatchLaterLink(linkId) {
  return request(`/recommendations/watch-later/links/${encodeURIComponent(linkId)}`, { method: 'DELETE' });
}
