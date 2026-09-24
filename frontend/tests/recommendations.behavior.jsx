import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';
import RecommendationPanel from '../src/components/RecommendationPanel.jsx';
import { RecommendationProvider } from '../src/components/RecommendationContext.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const report = [];
const suggestion = { id: 'youtube:abcdefghijk', title: 'River wildlife guide', source_url: 'https://www.youtube.com/watch?v=abcdefghijk', connector: 'youtube', uploader: 'Nature channel', duration: 123, thumbnail_url: null };
const defaultProviders = [
  { id: 'youtube', name: 'YouTube', domain: 'youtube.com', enabled: true },
  { id: 'rumble', name: 'Rumble', domain: 'rumble.com', enabled: true },
];
const vimeo = { id: 'vimeo.com', name: 'Vimeo', domain: 'vimeo.com', enabled: true };
const vimeoSuggestion = { ...suggestion, id: 'vimeo.com:123456789', title: 'Vimeo wildlife guide', source_url: 'https://vimeo.com/123456789', connector: 'vimeo.com', verified: false, verification: 'custom_search' };
let root, settings, models, calls, response, media, watchHistory, failure, modelsFailure, downloadFailure, pending, pendingPatch, respectVerificationSetting;
Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value() { return Promise.resolve(); } });
Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value() {} });
window.fetch = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  const body = options.body ? JSON.parse(options.body) : undefined;
  const method = options.method || 'GET';
  calls.push({ path: url.pathname, body, method, signal: options.signal });
  let value, error;
  if (url.pathname === '/api/recommendations/settings') {
    if (method === 'PATCH') {
      settings = { ...settings, ...body, revision: settings.revision + 1 };
      if (pendingPatch) await pendingPatch;
    }
    value = { ...settings };
  } else if (url.pathname === '/api/recommendations/models') {
    if (modelsFailure) error = 'Connector is unavailable';
    value = { models, connector_url: 'http://127.0.0.1:8301' };
  } else if (url.pathname === '/api/recommendations/configs') {
    const imported = { id: 'uploaded-routing', name: body.name };
    models = [...models, imported];
    value = { model_id: imported.id, name: imported.name };
  } else if (url.pathname.endsWith('/thumbnail-domains/detect')) {
    value = { domains: ['detected.vimeocdn.com'], page_url: 'https://vimeo.com/search?q=video' };
  } else if (url.pathname === '/api/recommendations') {
    if (failure) error = 'Model could not produce a playlist';
    value = pending ? await pending : response;
    if (respectVerificationSetting) value = { ...value, items: value.items.filter((item) => settings.allow_unverified_links || item.verified !== false) };
  } else if (url.pathname.startsWith('/api/settings/links/') && method === 'PATCH') {
    value = { id: Number(url.pathname.split('/').at(-1)), state: body.state };
  } else if (url.pathname === '/api/media') {
    if (method === 'POST') {
      const queued = { ...suggestion, id: 'downloaded-video', status: 'queued', source_url: body.urls[0], quality: body.quality };
      media = [{ ...queued, status: downloadFailure ? 'failed' : 'ready', error_message: downloadFailure, stream_url: '/tests/player.fixture.webm' }, ...media];
      value = [queued];
    } else value = media;
  } else if (url.pathname.startsWith('/api/media/')) value = media.find((item) => item.id === url.pathname.split('/').at(-1));
  else if (url.pathname === '/api/watch-history') value = method === 'GET' ? watchHistory : { recorded: true };
  else if (url.pathname === '/api/connectors') value = [];
  else throw new Error(`Unexpected test request ${method} ${path}`);
  return { ok: !error, status: error ? 502 : 200, json: async () => error ? { detail: error } : value };
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, got ${actual}`);
const find = (selector) => { const item = host.querySelector(selector); assert(item, `Missing ${selector}`); return item; };
const button = (text) => { const item = [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === text); assert(item, `Missing ${text}`); return item; };
const click = (item) => act(async () => item.click());
const navigate = (route) => act(async () => { location.hash = route; window.dispatchEvent(new HashChangeEvent('hashchange')); });
const toggle = () => find('.app-header input[role="switch"]');
const posts = (path) => calls.filter((call) => call.path === path && call.method === 'POST');
async function input(item, value) {
  await act(async () => {
    const proto = item instanceof HTMLSelectElement ? HTMLSelectElement.prototype : item instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(item, value);
    item.dispatchEvent(new Event(item instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}
async function mount(route = 'feed') {
  history.replaceState(null, '', `#${route}`);
  root = createRoot(host);
  await act(async () => root.render(<App />));
}
async function mountPanel(source) {
  root = createRoot(host);
  await act(async () => root.render(<RecommendationProvider><RecommendationPanel context="history" source={source} /></RecommendationProvider>));
}
async function test(name, body) {
  settings = { enabled: false, model_id: 'my-config', seed_keywords: [], providers: defaultProviders.map((provider) => ({ ...provider })), allow_unverified_links: false, allow_ai_title_lookup: false, revision: 0 };
  models = [{ id: 'my-config', name: 'My config' }, { id: 'second-model', name: 'Second model' }];
  calls = []; media = []; watchHistory = [{ video_id: 'watched-youtube', ...suggestion, thumbnail_url: '/api/media/watched-youtube/thumbnail', last_watched_at: '2026-09-20T12:00:00Z', position_seconds: 60 }]; failure = modelsFailure = respectVerificationSetting = false; downloadFailure = null; pending = pendingPatch = null; root = null;
  response = { status: 'ready', items: [suggestion], keywords: ['wildlife'], warnings: [] };
  try { await body(); report.push({ name, passed: true }); }
  catch (error) { report.push({ name, passed: false, message: error.message }); }
  finally { if (root) await act(async () => root.unmount()); host.replaceChildren(); }
  document.getElementById('results').textContent = report.map((item) => `${item.passed ? 'PASS' : 'FAIL'} ${item.name}${item.message ? ': ' + item.message : ''}`).join('\n');
}

await test('Disabled empty feed makes no inference or download request', async () => {
  await mount();
  assert(!toggle().checked, 'Switch defaults off');
  equal(host.querySelector('.recommendation-panel'), null, 'No disabled suggestions');
  equal(posts('/api/recommendations').length, 0, 'No inference');
  equal(posts('/api/media').length, 0, 'No download');
});
await test('Live-discovered models and interests persist and enable an empty feed', async () => {
  await mount('recommendations');
  equal(find('#recommendation-model').options.length, 3, 'Discovered model options');
  await input(find('#recommendation-model'), 'second-model');
  await click(button('Save model'));
  equal(settings.model_id, 'second-model', 'Selected model saved');
  await input(find('#recommendation-seeds'), 'wildlife, rivers');
  await click(button('Save interests'));
  equal(settings.seed_keywords.join(','), 'wildlife,rivers', 'Interests saved');
  await input(find('#recommendation-custom-prompt'), 'Prefer concise videos from new creators.');
  await click(button('Save hint'));
  equal(settings.custom_prompt, 'Prefer concise videos from new creators.', 'Custom model hint saved');
  await input(find('#recommendation-custom-prompt'), 'Include longer technical explanations.');
  await click(button('Save hint'));
  equal(settings.custom_prompt, 'Include longer technical explanations.', 'Custom model hint can be changed at any time');
  await click(toggle());
  await navigate('feed');
  equal(find('.recommendation-item h3').textContent, suggestion.title, 'Automatic empty feed suggestions');
  equal(posts('/api/recommendations').at(-1).body.context, 'feed', 'Feed context sent');
  equal(posts('/api/media').length, 0, 'Suggestions alone do not download');
});
await test('Missing configuration redirects the header switch to settings', async () => {
  settings.model_id = null;
  await mount();
  await click(toggle());
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(location.hash, '#recommendations', 'Configure before enable');
  assert(!settings.enabled, 'Not accidentally enabled');
});
await test('A native config upload is registered and selected without enabling inference', async () => {
  await mount('recommendations');
  const config = { sequences: [{ provider: 'mock', model: 'mock-assistant', retries: 0 }] };
  const file = new File([JSON.stringify(config)], 'config.json', { type: 'application/json' });
  const transfer = new DataTransfer(); transfer.items.add(file);
  await act(async () => { find('#recommendation-config-file').files = transfer.files; find('#recommendation-config-file').dispatchEvent(new Event('change', { bubbles: true })); });
  await input(find('#recommendation-config-name'), 'My uploaded config');
  await click(button('Import & select'));
  for (let i = 0; i < 20 && settings.model_id !== 'uploaded-routing'; i++) await act(async () => new Promise((done) => setTimeout(done, 10)));
  equal(posts('/api/recommendations/configs')[0].body.config.sequences[0].model, 'mock-assistant', 'Uploaded JSON contents sent');
  equal(settings.model_id, 'uploaded-routing', 'New config selected');
  assert(!settings.enabled, 'Import does not enable inference');
});
await test('AI title fallback is explicit and requires a selected model', async () => {
  await mount('recommendations');
  const control = find('#recommendation-ai-titles-heading').closest('section').querySelector('input[role="switch"]');
  assert(!control.disabled && !control.checked, 'Configured model exposes an off-by-default title switch');
  await click(control);
  assert(settings.allow_ai_title_lookup, 'AI title preference saved');
  await act(async () => root.unmount()); root = null; host.replaceChildren();
  settings.model_id = null;
  await mount('recommendations');
  const unavailable = find('#recommendation-ai-titles-heading').closest('section').querySelector('input[role="switch"]');
  assert(unavailable.disabled, 'Title AI cannot be enabled without a selected model');
});
await test('Cold start explains how to seed recommendations', async () => {
  settings.enabled = true;
  response = { status: 'needs_history', items: [], keywords: [], warnings: [] };
  await mount();
  assert(find('.recommendation-panel').textContent.includes('Watch a video or add a few interests'), 'Cold start explained');
  await click(button('Add your interests'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(location.hash, '#recommendations', 'Interests link opens settings');
});
await test('Recommendation download stays on the current screen until Play is clicked', async () => {
  settings.enabled = true;
  await mount();
  await click(button('Download'));
  equal(posts('/api/media').length, 1, 'Exactly one download queued');
  equal(posts('/api/media')[0].body.urls[0], suggestion.source_url, 'Verified URL used');
  equal(location.hash, '#feed', 'Completed download does not redirect');
  assert(find('.recommendation-download-state').textContent.includes('Download complete'), 'Completion shown in place');
  await click(button('Play'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(location.hash, '#feed/downloaded-video', 'Explicit Play opens ready media');
});
await test('A failed recommendation download displays the backend reason and can be retried', async () => {
  settings.enabled = true;
  downloadFailure = 'The source temporarily refused the download. Please try again.';
  await mount();
  await click(button('Download'));
  equal(find('.recommendation-download-error').textContent, downloadFailure, 'Backend error_message shown');
  assert(!button('Retry download').disabled, 'Failed download exposes an enabled retry');
  equal(location.hash, '#feed', 'A failed download does not start playback');
  downloadFailure = null;
  await click(button('Retry download'));
  equal(posts('/api/media').length, 2, 'Retry queues a new download');
  equal(location.hash, '#feed', 'Successful retry remains on the recommendation screen');
});
await test('Watch shows suggestions alongside saved Up next and plays saved results without downloading', async () => {
  settings.enabled = true;
  media = [
    { ...suggestion, id: 'current', title: 'Current video', status: 'ready', stream_url: '/tests/player.fixture.webm' },
    { ...suggestion, id: 'saved-next', title: 'Saved recommendation', status: 'ready', stream_url: '/tests/player.fixture.webm' },
  ];
  response.items = [{ ...suggestion, media_id: 'saved-next' }];
  await mount('watch/current');
  equal(posts('/api/recommendations').at(-1).body.video_id, 'current', 'Current video informs recommendations');
  assert(find('.cf-upnext-item'), 'Local Up next preserved');
  await click(find('.recommendation-item button'));
  equal(posts('/api/media').length, 0, 'Existing media reused');
  equal(location.hash, '#watch/saved-next', 'Saved recommendation opened');
});
await test('Each recommendation can download independently', async () => {
  settings.enabled = true;
  const second = { ...suggestion, id: 'rumble:v123abc', title: 'Second suggestion', connector: 'rumble', source_url: 'https://rumble.com/v123abc-nature.html' };
  response.items = [suggestion, second];
  await mount('watch');
  const buttons = [...host.querySelectorAll('.recommendation-item-actions > button')];
  await click(buttons[0]);
  await click(buttons[1]);
  equal(posts('/api/media').length, 2, 'Both downloads queued without leaving the page');
  equal(location.hash, '#watch', 'Multiple completed downloads do not redirect');
});
await test('A recommendation is marked repeated only after a user action', async () => {
  settings.enabled = true;
  response.items = [{ ...suggestion, link_id: 77 }];
  await mount('watch');
  await click(button('Mark repeated'));
  const request = calls.find((call) => call.path === '/api/settings/links/77');
  equal(request.body.state, 'silenced', 'User action explicitly classifies the link');
  assert(button('Marked repeated').disabled, 'Recommendation displays the reviewed state');
});
await test('YouTube watch history keeps recommendations across enabled websites when its list is filtered', async () => {
  settings.enabled = true;
  settings.providers.push(vimeo);
  response.items = [vimeoSuggestion];
  await mount('watch-history');
  equal(find('.watch-history-thumbnail img').getAttribute('src'), '/api/media/watched-youtube/thumbnail', 'History row renders its local thumbnail endpoint');
  equal(posts('/api/recommendations').at(-1).body.context, 'history', 'History context sent');
  equal(posts('/api/recommendations').at(-1).body.source, 'all', 'YouTube history permits all enabled recommendation websites');
  equal(find('.recommendation-item .connector-badge').textContent, 'Vimeo', 'Another website can be recommended from YouTube history');
  const count = posts('/api/recommendations').length;
  await click(button('YouTube'));
  equal(find('.watch-history-item .connector-badge').textContent, 'YouTube', 'History website filter applies to watched rows');
  await click(button('Rumble'));
  equal(host.querySelector('.watch-history-item'), null, 'History list changes to selected website');
  equal(posts('/api/recommendations').length, count, 'History list filtering does not rerun or narrow recommendations');
  equal(find('.recommendation-item .connector-badge').textContent, 'Vimeo', 'Cross-website recommendation stays visible');
});
await test('A removed watch-history video can be downloaded again without redirecting', async () => {
  await mount('watch-history');
  await click(button('Download again'));
  equal(posts('/api/media').length, 1, 'Original history URL queued again');
  equal(posts('/api/media')[0].body.urls[0], suggestion.source_url, 'Saved source URL reused');
  equal(location.hash, '#watch-history', 'Queueing from history stays on history');
  assert(button('View download'), 'Queued download is linked from the history row');
});
await test('Unverified model links require an explicit settings toggle', async () => {
  await mount('recommendations');
  const control = find('#recommendation-links-heading').closest('section').querySelector('input[role="switch"]');
  assert(!control.checked, 'Unverified links default off');
  await click(control);
  assert(settings.allow_unverified_links, 'Unverified-link preference saved');
});
await test('Custom websites can be added, restored, disabled, and removed', async () => {
  await mount('recommendations');
  await input(find('#recommendation-provider-domain'), 'https://www.Vimeo.com/');
  await input(find('#recommendation-provider-name'), 'Vimeo');
  await click(button('Add website'));
  equal(settings.providers.length, 3, 'Custom provider persisted alongside builtins');
  equal(settings.providers.at(-1).id, 'vimeo.com', 'Canonical domain used as custom provider id');
  equal(settings.providers.at(-1).domain, 'vimeo.com', 'Pasted website normalized');
  equal(find('#recommendation-provider-domain').value, '', 'Successful form clears domain');
  assert(find('.success-banner').textContent.includes('Enable Show unverified links'), 'Custom discovery preference explained');
  await act(async () => root.unmount()); root = null;
  await mount('recommendations');
  assert(find('[aria-label="Recommendations from Vimeo"]').checked, 'Saved custom website restored');
  await click(find('[aria-label="Recommendations from Vimeo"]'));
  assert(!settings.providers.find((provider) => provider.id === 'vimeo.com').enabled, 'Custom website disabled');
  await click(find('[aria-label="Remove Vimeo"]'));
  equal(settings.providers.length, 2, 'Custom website removed');
  equal(host.querySelector('[aria-label="Remove YouTube"]'), null, 'Builtin websites remain available');
});
await test('Custom search URLs can be added, edited, and reset without losing provider fields', async () => {
  await mount('recommendations');
  await input(find('#recommendation-provider-domain'), 'vimeo.com');
  await input(find('#recommendation-provider-name'), 'Vimeo');
  await input(find('#recommendation-provider-search-url'), 'https://vimeo.com/search?q={query}');
  await input(find('#recommendation-provider-thumbnail-domains'), 'i.vimeocdn.com, vimeocdn.com');
  await click(button('Add website'));
  equal(settings.providers.at(-1).search_url, 'https://vimeo.com/search?q={query}', 'New custom search template is submitted');
  equal(settings.providers.at(-1).thumbnail_domains.join(','), 'i.vimeocdn.com,vimeocdn.com', 'New thumbnail CDN domains are submitted');
  const row = find('[data-provider="vimeo.com"]');
  await click(row.querySelector('summary'));
  await click(button('Detect from website'));
  assert(find('[aria-label="Thumbnail CDN domains for Vimeo"]').value.includes('detected.vimeocdn.com'), 'Detected domain is suggested in the editable field');
  equal(settings.providers.at(-1).thumbnail_domains.join(','), 'i.vimeocdn.com,vimeocdn.com', 'Detection does not trust or save candidates automatically');
  await input(find('[aria-label="Search URL for Vimeo"]'), 'https://vimeo.com/search?q=');
  await input(find('[aria-label="Thumbnail CDN domains for Vimeo"]'), 'cdn.vimeo-assets.com');
  await click(row.querySelector('.provider-search-settings button'));
  equal(settings.providers.at(-1).search_url, 'https://vimeo.com/search?q=', 'Existing provider prefix is submitted');
  equal(settings.providers.at(-1).thumbnail_domains.join(','), 'cdn.vimeo-assets.com', 'Existing thumbnail CDN domains are submitted');
  equal(settings.providers.at(-1).name, 'Vimeo', 'Editing preserves provider name');
  assert(settings.providers.at(-1).enabled, 'Editing preserves provider enabled state');
  equal(settings.providers.length, 3, 'Editing preserves other providers');
  await input(find('[aria-label="Search URL for Vimeo"]'), '');
  await click(row.querySelector('.provider-search-settings button'));
  equal(settings.providers.at(-1).search_url, null, 'Empty search URL resets to default');
  equal(host.querySelector('[aria-label="Search URL for YouTube"]'), null, 'Builtins keep native search controls unchanged');
});
await test('Fallback percentages persist and reject totals other than 100 percent', async () => {
  await mount('recommendations');
  equal(find('[aria-label="Website search fallback percent"]').value, '50', 'Default website share');
  equal(find('[aria-label="Watch later fallback percent"]').value, '50', 'Default saved share');
  await input(find('[aria-label="Website search fallback percent"]'), '60');
  const patches = () => calls.filter((call) => call.path === '/api/recommendations/settings' && call.method === 'PATCH');
  const before = patches().length;
  await click(button('Save fallback mix'));
  equal(patches().length, before, 'Invalid total is not saved');
  assert(host.textContent.includes('add up to 100%'), 'Invalid total is explained');
  await input(find('[aria-label="Watch later fallback percent"]'), '40');
  await click(button('Save fallback mix'));
  equal(JSON.stringify(settings.fallback_weights), JSON.stringify({ custom_search: 60, watch_later: 40, public_search: 0 }), 'Valid fallback mix saved');
  await navigate('feed');
  await navigate('recommendations');
  equal(find('[aria-label="Watch later fallback percent"]').value, '40', 'Saved weights survive navigation');
});
await test('Recommendation search can switch between selected and all page links', async () => {
  await mount('recommendations');
  const toggle = find('[aria-label="Fetch all search-page links for recommendations"]');
  assert(!toggle.checked, 'Selected-link mode is the default');
  await click(toggle);
  equal(settings.fetch_all_search_links, true, 'Fetch-all recommendation preference persists');
  equal(calls.filter((call) => call.path === '/api/recommendations/settings' && call.method === 'PATCH').at(-1).body.fetch_all_search_links, true, 'Toggle sends the fetch-all setting');
});
await test('Website names default to the domain and duplicate or video URLs are rejected', async () => {
  await mount('recommendations');
  await input(find('#recommendation-provider-domain'), 'bilibili.tv');
  await click(button('Add website'));
  equal(settings.providers.at(-1).name, 'bilibili.tv', 'Optional name defaults to domain');
  await input(find('#recommendation-provider-domain'), 'www.bilibili.tv');
  await click(button('Add website'));
  equal(settings.providers.length, 3, 'Duplicate normalized domain not saved');
  assert(host.textContent.includes('already in your recommendation providers'), 'Duplicate explained');
  await input(find('#recommendation-provider-domain'), 'vimeo.com/123456789');
  await click(button('Add website'));
  equal(settings.providers.length, 3, 'Video URL not added as a website');
  assert(host.textContent.includes('Enter a website domain'), 'Domain requirement explained');
});
await test('Enabled unverified results render after saving the toggle and hide when turned off', async () => {
  settings.enabled = true;
  settings.providers.push(vimeo);
  response.items = [vimeoSuggestion];
  respectVerificationSetting = true;
  await mount('feed');
  equal(host.querySelector('.recommendation-item'), null, 'Unverified results start hidden');
  await navigate('recommendations');
  const unverifiedControl = () => find('#recommendation-links-heading').closest('section').querySelector('input[role="switch"]');
  await click(unverifiedControl());
  await navigate('feed');
  equal(find('.recommendation-item h3').textContent, vimeoSuggestion.title, 'Fresh unverified result is displayed');
  equal(find('.recommendation-unverified').textContent, 'Unverified link', 'Unverified status remains visible');
  equal(find('.recommendation-item .connector-badge').textContent, 'Vimeo', 'Configured name labels result');
  await navigate('recommendations');
  await click(unverifiedControl());
  await navigate('feed');
  equal(host.querySelector('.recommendation-item'), null, 'Previously displayed unverified item is removed');
});
await test('Custom results offer original links and download through the existing queue', async () => {
  settings.enabled = settings.allow_unverified_links = true;
  settings.providers.push(vimeo);
  response.items = [vimeoSuggestion];
  await mount('feed');
  const original = find('.recommendation-item a');
  equal(original.href, vimeoSuggestion.source_url, 'Original video URL preserved');
  equal(original.target, '_blank', 'Original opens in a new tab');
  assert(original.relList.contains('noopener') && original.relList.contains('noreferrer'), 'Original uses isolated tab');
  await click(button('Download'));
  equal(posts('/api/media')[0].body.urls[0], vimeoSuggestion.source_url, 'Custom video URL is queued for generic download');
  equal(location.hash, '#feed', 'Custom download stays on the feed');
});
await test('History maps generic downloads to website labels without restricting recommendations', async () => {
  settings.enabled = settings.allow_unverified_links = true;
  settings.providers.push(vimeo);
  watchHistory = [{ ...watchHistory[0], ...vimeoSuggestion, connector: 'generic' }];
  response.items = [vimeoSuggestion];
  await mount('watch-history');
  equal(posts('/api/recommendations').at(-1).body.source, 'all', 'Generic history discovers across websites');
  equal(find('.watch-history-item .connector-badge').textContent, 'Vimeo', 'History row uses configured website name');
  await click(button('YouTube'));
  equal(host.querySelector('.watch-history-item'), null, 'Builtin history filter remains selectable');
  await click(button('Vimeo'));
  equal(find('.watch-history-item .connector-badge').textContent, 'Vimeo', 'Custom website filters watched rows');
  equal(posts('/api/recommendations').at(-1).body.source, 'all', 'Custom history filter does not narrow recommendations');
});
await test('YouTube Watch can display custom website recommendations by default', async () => {
  settings.enabled = settings.allow_unverified_links = true;
  settings.providers.push(vimeo);
  response.items = [vimeoSuggestion];
  media = [{ ...suggestion, id: 'youtube-current', status: 'ready', stream_url: '/tests/player.fixture.webm' }];
  await mount('watch/youtube-current');
  equal(posts('/api/recommendations').at(-1).body.source, 'all', 'Current YouTube website does not limit recommendation sources');
  equal(posts('/api/recommendations').at(-1).body.video_id, 'youtube-current', 'Current video still informs recommendations');
  equal(find('.recommendation-item .connector-badge').textContent, 'Vimeo', 'Custom website suggestion appears beside YouTube video');
  equal(find('select[aria-label="Recommendation websites"]').value, 'all', 'Recommendation filter defaults to all enabled websites');
});
await test('Watching a generic download also keeps recommendations across websites', async () => {
  settings.enabled = true;
  settings.providers.push(vimeo);
  media = [{ ...vimeoSuggestion, connector: 'generic', id: 'custom-current', status: 'ready', stream_url: '/tests/player.fixture.webm' }];
  await mount('watch/custom-current');
  equal(posts('/api/recommendations').at(-1).body.source, 'all', 'Generic download does not limit recommendation sources');
  equal(find('.recommendation-item .connector-badge').textContent, 'YouTube', 'A different website appears for a generic download');
});
await test('Recommendation website filter is optional and offers only enabled providers', async () => {
  settings.enabled = true;
  settings.providers[1].enabled = false;
  settings.providers.push(vimeo);
  await mount('watch-history');
  const select = find('select[aria-label="Recommendation websites"]');
  equal([...select.options].map((option) => option.value).join(','), 'all,youtube,vimeo.com', 'Only enabled providers and all websites can be selected');
  response.items = [vimeoSuggestion];
  await input(select, 'vimeo.com');
  equal(posts('/api/recommendations').at(-1).body.source, 'vimeo.com', 'Explicit custom selection narrows recommendation source');
  equal(find('.recommendation-item .connector-badge').textContent, 'Vimeo', 'Filtered recommendations shown');
  equal(find('.watch-history-item .connector-badge').textContent, 'YouTube', 'Recommendation filter does not change watched list');
  await input(select, 'all');
  equal(posts('/api/recommendations').at(-1).body.source, 'all', 'All enabled websites restores cross-website discovery');
});
await test('Explicit panel source remains scoped and disabled sources never fall back to all', async () => {
  settings.enabled = true;
  settings.providers.push({ ...vimeo, enabled: false });
  await mountPanel('youtube');
  equal(posts('/api/recommendations').at(-1).body.source, 'youtube', 'Explicit source remains authoritative');
  equal(host.querySelector('select[aria-label="Recommendation websites"]'), null, 'Fixed scope has no conflicting selector');
  const count = posts('/api/recommendations').length;
  await act(async () => root.render(<RecommendationProvider><RecommendationPanel context="history" source="vimeo.com" /></RecommendationProvider>));
  equal(posts('/api/recommendations').length, count, 'Explicit disabled source makes no unscoped recommendation request');
  assert(find('.recommendation-panel').textContent.includes('Enable Vimeo in recommendation settings'), 'Disabled source explained');
  equal(host.querySelector('.recommendation-item'), null, 'Results from previous source disappear');
});
await test('A late result cannot replace recommendations after their website filter changes', async () => {
  settings.enabled = true;
  settings.providers.push(vimeo);
  let release;
  pending = new Promise((resolve) => { release = resolve; });
  await mount('feed');
  const previous = posts('/api/recommendations').at(-1);
  pending = null;
  response.items = [vimeoSuggestion];
  await input(find('select[aria-label="Recommendation websites"]'), 'vimeo.com');
  await act(async () => release({ ...response, items: [suggestion] }));
  assert(previous.signal.aborted, 'Old website request is aborted');
  equal(find('.recommendation-item .connector-badge').textContent, 'Vimeo', 'Late all-websites result cannot overwrite current choice');
});
await test('No enabled websites explains the setting without starting inference', async () => {
  settings.enabled = true;
  settings.providers = settings.providers.map((provider) => ({ ...provider, enabled: false }));
  await mount('feed');
  equal(posts('/api/recommendations').length, 0, 'No inference for empty provider scope');
  assert(host.textContent.includes('Enable at least one website'), 'Provider setup explained');
});
await test('Disabling hides recommendations immediately and ignores a late model response', async () => {
  settings.enabled = true;
  let release, releasePatch;
  pending = new Promise((resolve) => { release = resolve; });
  await mount();
  pendingPatch = new Promise((resolve) => { releasePatch = resolve; });
  await click(toggle());
  equal(host.querySelector('.recommendation-panel'), null, 'Off hides loading before PATCH completes');
  await act(async () => release(response));
  equal(host.querySelector('.recommendation-panel'), null, 'Late results ignored');
  assert(posts('/api/recommendations')[0].signal.aborted, 'In-flight fetch aborted');
  await act(async () => releasePatch());
  assert(!toggle().checked, 'Off state persisted');
  equal(posts('/api/media').length, 0, 'Late result never downloaded');
});
await test('Recommendation failures expose a retry and discovery outages recover', async () => {
  settings.enabled = true; failure = true;
  await mount();
  assert(find('.recommendation-panel [role="alert"]').textContent.includes('Model could not produce'), 'Inference error shown');
  failure = false;
  await click(button('Try again'));
  equal(find('.recommendation-item h3').textContent, suggestion.title, 'Inference retry recovered');
  assert(posts('/api/recommendations').at(-1).body.refresh, 'Retry requests fresh results');
  modelsFailure = true;
  await navigate('recommendations');
  assert(host.textContent.includes('Connector is unavailable'), 'Discovery outage shown');
  modelsFailure = false;
  await click(button('Retry connection'));
  equal(find('#recommendation-model').options.length, 3, 'Discovery retry recovered');
});
await test('Partial search failures keep successful videos and explain each source in collapsed details', async () => {
  settings.enabled = true;
  settings.providers.push(vimeo, { id: 'instagram.com', name: 'Instagram', domain: 'instagram.com', enabled: false });
  const sharedWarning = 'One website could not complete its search.';
  response.warnings = [sharedWarning];
  response.sources = [
    { id: 'youtube', name: 'YouTube', status: 'ok', count: 4, discovery: 'provider_search', warnings: [] },
    { id: 'rumble', name: 'Rumble', status: 'unavailable', count: 0, discovery: 'custom_search', warnings: [sharedWarning, 'Try refreshing again later.'] },
    { id: 'vimeo.com', name: 'Vimeo', status: 'empty', count: 0, discovery: 'native', warnings: [] },
    { id: 'instagram.com', name: 'Instagram', status: 'unavailable', count: 0, discovery: 'custom_search', warnings: [] },
  ];
  await mount('feed');
  equal(find('.recommendation-item h3').textContent, suggestion.title, 'Successful recommendations remain visible during a partial failure');
  assert(!button('Download').disabled, 'Successful recommendation remains usable');
  equal(host.querySelector('.recommendation-panel [role="alert"]'), null, 'A partial source failure is not a panel error');
  const details = find('.recommendation-search-details');
  assert(!details.open, 'Search details start collapsed');
  await click(details.querySelector('summary'));
  assert(details.open, 'Search details can be expanded');
  equal(details.querySelectorAll('li').length, 3, 'Only enabled searched websites appear');
  assert(details.querySelector('[data-source="youtube"]').textContent.includes('Found 4 videos'), 'Successful source count shown');
  assert(details.querySelector('[data-source="youtube"]').textContent.includes('Website search'), 'Source video search described');
  assert(details.querySelector('[data-source="rumble"]').textContent.includes('Search temporarily unavailable'), 'Unavailable search has friendly status');
  assert(details.querySelector('[data-source="rumble"]').textContent.includes('Website search'), 'Website discovery described');
  assert(details.querySelector('[data-source="vimeo.com"]').textContent.includes('No matches'), 'Empty results differ from failure');
  equal(host.textContent.split(sharedWarning).length - 1, 1, 'Warning repeated in source and response is shown only once');
  equal(details.querySelector('a, button'), null, 'Search details do not add external search actions');
});
await test('Recent search results are explained without changing the video verification label', async () => {
  settings.enabled = true;
  response.items = [{ ...suggestion, verified: true }];
  response.sources = [{ id: 'youtube', name: 'YouTube', status: 'cached', count: 1, discovery: 'provider_search', warnings: ['Live search is temporarily unavailable.'] }];
  await mount('feed');
  const details = find('.recommendation-search-details');
  await click(details.querySelector('summary'));
  assert(details.textContent.includes('Using recent results · 1 video'), 'Cached source and candidate count explained');
  assert(details.textContent.includes('Live search is temporarily unavailable.'), 'Source warning remains available in details');
  equal(find('.recommendation-item h3').textContent, suggestion.title, 'Recent recommendation remains displayed');
  equal(host.querySelector('.recommendation-unverified'), null, 'Cache fallback does not mark verified video unverified');
  assert(!button('Download').disabled, 'Recent recommendation is usable');
});
await test('Responses without search diagnostics remain compatible', async () => {
  settings.enabled = true;
  delete response.sources;
  await mount('feed');
  equal(find('.recommendation-item h3').textContent, suggestion.title, 'Older response still renders recommendations');
  equal(host.querySelector('.recommendation-search-details'), null, 'Missing source details do not create empty diagnostics');
});

const passed = report.filter((item) => item.passed).length;
document.body.dataset.testStatus = passed === report.length ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${passed}/${report.length} passed`;
