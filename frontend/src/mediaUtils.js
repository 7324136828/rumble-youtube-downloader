export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return '';
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = String(total % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${remainder}` : `${minutes}:${remainder}`;
}
export function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
const CONNECTOR_LABELS = { youtube: 'YouTube', rumble: 'Rumble', generic: 'Other', upload: 'Upload' };
export function connectorLabel(id) { return CONNECTOR_LABELS[id] || id || 'Other'; }
export function connectorBadgeClass(id) { return `connector-badge badge-${Object.hasOwn(CONNECTOR_LABELS, id) ? id : 'generic'}`; }
export function loadLikes() {
  try { const parsed = JSON.parse(localStorage.getItem('clipfeed.likes') || '[]'); return new Set(Array.isArray(parsed) ? parsed : []); } catch { return new Set(); }
}
export function saveLikes(likes) {
  try { localStorage.setItem('clipfeed.likes', JSON.stringify([...likes])); } catch { /* Preferences still work for this session when storage is unavailable. */ }
}
export function toggleLike(likes, id) {
  const next = new Set(likes);
  if (next.has(id)) next.delete(id); else next.add(id);
  saveLikes(next);
  return next;
}
export function getPlayerMode() {
  try { return localStorage.getItem('clipfeed.playerMode') === 'watch' ? 'watch' : 'feed'; } catch { return 'feed'; }
}
export function setPlayerMode(mode) {
  try { localStorage.setItem('clipfeed.playerMode', mode); } catch { /* Optional persistence. */ }
}

export function mediaPlayback(video) {
  const mp3 = video?.conversions?.mp3;
  const mp4 = video?.conversions?.mp4;
  const mp3Ready = mp3?.status === 'completed' && Boolean(mp3.stream_url);
  const mp4Ready = mp4?.status === 'completed' && Boolean(mp4.stream_url);
  const explicit = Boolean(video?.playback_preference_explicit);
  const useMp3 = mp3Ready && (video?.playback_format === 'mp3' || (!explicit && !mp4Ready));
  const useMp4 = mp4Ready && !(explicit && video?.playback_format === 'mp3');
  return {
    src: useMp3 ? mp3.stream_url : useMp4 ? mp4.stream_url : video?.stream_url,
    audioOnly: useMp3 || (video?.media_kind === 'audio' && !useMp4),
  };
}

