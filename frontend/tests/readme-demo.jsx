// A local, privacy-safe fixture for README captures. No backend or external URLs.
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';
import '../src/App.css';

const providers = [
  { id: 'youtube', name: 'YouTube', domain: 'youtube.com', enabled: true },
  { id: 'rumble', name: 'Rumble', domain: 'rumble.com', enabled: true },
];
const recommendationSettings = {
  enabled: true, model_id: 'demo-model', seed_keywords: ['hiking', 'city walks'],
  custom_prompt: 'Suggest calm outdoor videos and thoughtful city walks.',
  providers, revision: 1, allow_unverified_links: false,
};
const videos = [
  { id: 'demo-mountain', title: 'Morning on the mountain trail', uploader: 'Trail Journal', connector: 'youtube', duration: 481, keywords: ['mountains', 'hiking', 'sunrise'], source_url: 'https://example.com/demo-mountain' },
  { id: 'demo-city', title: 'A quiet walk through the city', uploader: 'City Walks', connector: 'rumble', duration: 738, keywords: ['city walks', 'architecture', 'night'], source_url: 'https://example.com/demo-city' },
  { id: 'demo-ocean', title: 'Ocean light at sunrise', uploader: 'Nature Notes', connector: 'youtube', duration: 356, keywords: ['ocean', 'sunrise', 'relaxation'], source_url: 'https://example.com/demo-ocean' },
];
const keywordCounts = [
  ['sunrise', 2], ['mountains', 1], ['hiking', 1], ['city walks', 1],
  ['architecture', 1], ['night', 1], ['ocean', 1], ['relaxation', 1],
];

window.fetch = async (input, options = {}) => {
  const url = new URL(input, window.location.origin);
  let value;
  if (url.pathname === '/api/recommendations/settings') value = recommendationSettings;
  else if (url.pathname === '/api/recommendations/models') value = {
    models: [{ id: 'demo-model', name: 'Demo model', description: 'Example local configuration' }],
  };
  else if (url.pathname === '/api/settings/downloads') value = {
    convert_for_browser: false, generate_thumbnails: true, auto_delete_enabled: true,
    retention_days: 7, cookie_browser: 'edge', cookie_browser_profile: 'Demo profile', cookie_file: '',
  };
  else if (url.pathname === '/api/settings/links') value = {
    hide_repeated_links: true, items: [], total: 0, limit: 200, offset: 0,
  };
  else if (url.pathname === '/api/video-keywords') {
    const query = (url.searchParams.get('q') || '').toLowerCase();
    value = {
      query, keywords: keywordCounts.map(([keyword, count]) => ({ keyword, count })),
      videos: videos.filter((video) => !query || video.keywords.some((keyword) => keyword.includes(query))),
      status: { ready: videos.length, pending: 0 },
    };
  } else if (url.pathname === '/api/connectors') value = [
    { id: 'youtube', name: 'YouTube' }, { id: 'rumble', name: 'Rumble' },
  ];
  else throw new Error(`Unexpected demo request: ${options.method || 'GET'} ${url.pathname}`);
  return { ok: true, status: 200, json: async () => value };
};

createRoot(document.getElementById('root')).render(<App />);
