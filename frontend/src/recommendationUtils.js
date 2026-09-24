import { connectorLabel } from './mediaUtils';

export const DEFAULT_FALLBACK_WEIGHTS = { custom_search: 50, public_search: 0, watch_later: 50 };

export const DEFAULT_RECOMMENDATION_PROVIDERS = [
  { id: 'youtube', name: 'YouTube', domain: 'youtube.com', enabled: true },
  { id: 'rumble', name: 'Rumble', domain: 'rumble.com', enabled: true },
];

export function recommendationProviderLabel(id, providers) {
  return providers.find((provider) => provider.id === id)?.name || connectorLabel(id);
}

export function recommendationProviderIcon(id) {
  return ['youtube', 'rumble'].includes(id) ? id : 'globe';
}

export function recommendationSource(video, providers) {
  if (providers.some((provider) => provider.id === video.connector)) return video.connector;
  try {
    const hostname = new URL(video.source_url).hostname.toLowerCase();
    const provider = providers.find(({ domain }) => hostname === domain || hostname.endsWith(`.${domain}`));
    if (provider) return provider.id;
  } catch { /* Older history entries may not have a source URL. */ }
  return video.connector || 'all';
}
