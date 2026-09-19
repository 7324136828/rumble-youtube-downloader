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

export function getMediaItems(status) {
  return request(status ? `/media?status=${encodeURIComponent(status)}` : '/media');
}

export function getMediaItem(id) {
  return request(`/media/${id}`);
}

export function deleteMedia(id) {
  return request(`/media/${id}`, { method: 'DELETE' });
}
