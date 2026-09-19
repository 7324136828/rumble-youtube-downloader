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
const CONNECTOR_LABELS = { youtube: 'YouTube', rumble: 'Rumble', generic: 'Other' };
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

