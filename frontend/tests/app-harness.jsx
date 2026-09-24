import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';
import SearchPage from '../src/components/SearchPage.jsx';
import { RecommendationProvider, useRecommendations } from '../src/components/RecommendationContext.jsx';

// All API calls are intercepted; these checks never contact a backend or download media.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const results = [];
const connectors = [{ id: 'youtube', name: 'YouTube' }, { id: 'rumble', name: 'Rumble' }, { id: 'generic', name: 'Other platforms' }];
const seed = [
  { id: 'youtube-one', title: 'Mountain morning', uploader: 'Trail channel', connector: 'youtube', created_at: '2026-09-19T12:00:00' },
  { id: 'rumble-two', title: 'City at night', uploader: 'City channel', connector: 'rumble', created_at: '2026-09-18T12:00:00' },
  { id: 'generic-three', title: 'Ocean sounds', uploader: 'Quiet channel', connector: 'generic', created_at: '2026-09-17T12:00:00' },
].map((item) => ({ ...item, status: 'ready', source_url: `https://example.com/${item.id}`, stream_url: '/tests/player.fixture.webm', download_url: '/tests/player.fixture.webm', duration: 120, file_size: 1024 }));
const searchSeed = [
  { id: 'youtube-search-one', link_id: 101, title: 'Mountain trail guide', source_url: 'https://www.youtube.com/watch?v=search-one', connector: 'youtube', uploader: 'Trail explorer', duration: 125, thumbnail_url: null },
  { id: 'rumble-search-two', link_id: 102, title: 'Mountain sunset walk', source_url: 'https://rumble.com/v-search-two.html', connector: 'rumble', uploader: 'Sunset channel', duration: 240, thumbnail_url: null },
];
const keywordSeed = [
  { ...seed[0], keywords: ['mountains', 'sunrise', 'hiking'] },
  { ...seed[1], keywords: ['city', 'night photography'] },
  { ...seed[2], keywords: ['ocean', 'relaxation'] },
];
const defaultProviders = [
  { id: 'youtube', name: 'YouTube', domain: 'youtube.com', enabled: true },
  { id: 'rumble', name: 'Rumble', domain: 'rumble.com', enabled: true },
];
const customProvider = { id: 'vimeo.com', name: 'My Vimeo', domain: 'vimeo.com', enabled: true };
const customResult = { id: 'vimeo-123', title: 'A custom website video', source_url: 'https://vimeo.com/123456789', connector: 'vimeo.com', verified: false, verification: 'custom_search', thumbnail_url: null };
let root;
let items;
let calls;
let loadFails;
let enqueueFails;
let deleteFails;
let resolveFails;
let searchFails;
let searchResults;
let searchWarnings;
let searchHasMore;
let pendingSearches;
let recommendationSettings;
let pendingSettings;
let settingsFail;
window.confirm = () => true;
window.fetch = async (path, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : undefined;
  const method = options.method || 'GET';
  const url = new URL(path, window.location.origin);
  calls.push({ path: url.pathname, method, body, query: Object.fromEntries(url.searchParams), signal: options.signal });
  let response;
  let error;
  if (url.pathname === '/api/recommendations/settings') {
    if (method === 'PATCH') recommendationSettings = { ...recommendationSettings, ...body, revision: recommendationSettings.revision + 1 };
    if (settingsFail) error = 'Website settings could not be loaded';
    response = pendingSettings ? await pendingSettings : recommendationSettings;
  }
  else if (url.pathname === '/api/recommendations/watch-later' && method === 'POST') response = { added: body.videos.length, updated: 0, items: body.videos };
  else if (url.pathname.startsWith('/api/settings/links/') && method === 'PATCH') response = { id: Number(url.pathname.split('/').at(-1)), state: body.state };
  else if (url.pathname === '/api/recommendations/models') response = { models: [] };
  else if (url.pathname === '/api/watch-history') response = { recorded: true };
  else if (url.pathname === '/api/video-keywords') {
    const query = (url.searchParams.get('q') || '').toLowerCase();
    const counts = new Map();
    keywordSeed.forEach((video) => video.keywords.forEach((keyword) => counts.set(keyword, (counts.get(keyword) || 0) + 1)));
    response = {
      query,
      keywords: [...counts].map(([keyword, count]) => ({ keyword, count })),
      videos: keywordSeed.filter((video) => !query || video.keywords.some((keyword) => keyword.includes(query))),
      status: { ready: keywordSeed.length },
    };
  }
  else if (url.pathname === '/api/connectors') response = connectors;
  else if (url.pathname === '/api/search') {
    if (searchFails) error = 'Video search is temporarily unavailable';
    response = pendingSearches.has(url.searchParams.get('q'))
      ? await pendingSearches.get(url.searchParams.get('q'))
      : { query: url.searchParams.get('q'), source: url.searchParams.get('source'), results: searchResults.slice(0, url.searchParams.get('fetch_all') === 'true' ? 200 : Number(url.searchParams.get('limit') || 12)), warnings: searchWarnings, has_more: url.searchParams.get('fetch_all') !== 'true' && searchHasMore && Number(url.searchParams.get('limit') || 12) < 24 };
  }
  else if (url.pathname === '/api/resolve') {
    if (resolveFails) error = 'Connector service unavailable';
    response = body.urls.map((url) => ({ url, connector: url.includes('unsupported.invalid') ? null : connectors.find((item) => item.id === (url.includes('youtu') ? 'youtube' : url.includes('rumble') ? 'rumble' : 'generic')) }));
  } else if (url.pathname === '/api/media' && method === 'POST') {
    if (enqueueFails) error = 'Queue is temporarily unavailable';
    response = body.urls.map((url, index) => ({ id: `queued-${calls.length}-${index}`, source_url: url, title: `Queued video ${index + 1}`, connector: url.includes('youtu') ? 'youtube' : 'rumble', status: 'queued', progress: 0, quality: body.quality }));
    if (!error) items = [...response, ...items];
  } else if (url.pathname === '/api/media' && method === 'GET') {
    if (loadFails) error = 'Library server unavailable';
    response = url.searchParams.get('status') ? items.filter((item) => item.status === url.searchParams.get('status')) : items;
  } else if (url.pathname.startsWith('/api/media/') && method === 'DELETE') {
    if (deleteFails) error = 'Delete failed on server';
    else items = items.filter((item) => item.id !== url.pathname.split('/').at(-1));
    response = {};
  } else throw new Error(`Unexpected test request: ${method} ${path}`);
  return { ok: !error, status: error ? 500 : 200, json: async () => error ? { detail: error } : response };
};
const assert = (value, message) => { if (!value) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, received ${actual}`);
const find = (selector) => { const item = host.querySelector(selector); assert(item, `Missing ${selector}`); return item; };
const textButton = (text, selector = 'button') => { const item = [...host.querySelectorAll(selector)].find((element) => element.textContent.trim() === text); assert(item, `Missing button: ${text}`); return item; };
const click = (element) => act(async () => element.click());
const wait = (ms) => act(async () => new Promise((done) => setTimeout(done, ms)));
const navigate = (route) => act(async () => { window.location.hash = route; window.dispatchEvent(new HashChangeEvent('hashchange')); });
const titles = () => [...host.querySelectorAll('.media-card h3')].map((item) => item.textContent).join('|');
const searchTitles = () => [...host.querySelectorAll('.search-result h3')].map((item) => item.textContent).join('|');
const keywordTitles = () => [...host.querySelectorAll('.keyword-video-card h3')].map((item) => item.textContent).join('|');
const searchCalls = () => calls.filter((call) => call.path === '/api/search');
async function input(element, value) {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}
async function submitOnlineSearch(query, source = 'all') {
  await input(find('input[aria-label="Search online videos"]'), query);
  await input(find('[aria-label="Search website"]'), source);
  await act(async () => {
    find('form[aria-label="Search online videos"]').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}
function ProviderSettingsDriver() {
  const { settings, updateSettings } = useRecommendations();
  return <><button onClick={() => updateSettings({ providers: settings.providers.filter((provider) => provider.id !== customProvider.id) }).catch(() => {})}>Remove test website</button><button onClick={() => updateSettings({ enabled: !settings.enabled, model_id: 'optional-test-model' }).catch(() => {})}>Change AI preference</button></>;
}
async function test(name, body, setup = () => {}) {
  items = seed.map((item) => ({ ...item })); calls = [];
  loadFails = enqueueFails = deleteFails = resolveFails = searchFails = false;
  searchResults = searchSeed.map((item) => ({ ...item })); searchWarnings = []; searchHasMore = false; pendingSearches = new Map();
  recommendationSettings = { enabled: false, model_id: null, seed_keywords: [], revision: 0, providers: defaultProviders.map((provider) => ({ ...provider })) };
  pendingSettings = null; settingsFail = false;
  localStorage.clear(); sessionStorage.clear();
  history.replaceState(null, '', '#library');
  setup();
  root = createRoot(host);
  try { await act(async () => root.render(<App />)); await body(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
  finally { await act(async () => root.unmount()); host.replaceChildren(); }
  document.getElementById('results').textContent = results.map((result) => `${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? `: ${result.error}` : ''}`).join('\n');
}

await test('Library source filters and case-insensitive search compose correctly', async () => {
  equal(host.querySelectorAll('.media-card').length, 3, 'Ready collection');
  await click(textButton('Rumble', '.source-filters button'));
  equal(titles(), 'City at night', 'Platform filter');
  await input(find('[aria-label="Search your library"]'), 'CITY');
  equal(titles(), 'City at night', 'Search matches title without case sensitivity');
  await input(find('[aria-label="Search your library"]'), 'mountain');
  equal(host.querySelectorAll('.media-card').length, 0, 'Search remains scoped to platform');
  await click(textButton('All videos', '.source-filters button'));
  equal(titles(), 'Mountain morning', 'All-platform search');
  await click(find('[aria-label="Clear search"]'));
  equal(host.querySelectorAll('.media-card').length, 3, 'Clear search restores collection');
});
await test('Library sorting changes visible order', async () => {
  await input(find('[aria-label="Sort videos"]'), 'oldest');
  equal(titles(), 'Ocean sounds|City at night|Mountain morning', 'Oldest sort');
  await input(find('[aria-label="Sort videos"]'), 'title');
  equal(titles(), 'City at night|Mountain morning|Ocean sounds', 'Title sort');
});
await test('Online video search routes from the library and preserves encoded keywords and platform', async () => {
  const query = 'mountain & river / café?';
  await submitOnlineSearch(query, 'rumble');
  assert(window.location.hash.startsWith('#search?'), 'Search opens a dedicated page');
  const route = new URLSearchParams(window.location.hash.split('?')[1]);
  equal(route.get('q'), query, 'Route retains reserved and Unicode characters');
  equal(route.get('source'), 'rumble', 'Route retains selected platform');
  const request = searchCalls().at(-1);
  assert(request, 'Search requested from backend');
  equal(request.method, 'GET', 'Search uses read-only request');
  equal(request.query.q, query, 'API receives original keywords');
  equal(request.query.source, 'rumble', 'API receives selected platform');
  equal(request.query.limit, '12', 'Search requests a bounded result count');
  equal(find('input[aria-label="Search online videos"]').value, query, 'Search input reflects the route');
  equal(find('[aria-label="Search website"]').value, 'rumble', 'Platform input reflects the route');
});
await test('Search navigation is available and blank queries do not request results', async () => {
  await click(textButton('Search videos', 'nav button'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(window.location.hash, '#search', 'Sidebar opens online search');
  equal(searchCalls().length, 0, 'Opening search does not submit a blank query');
  await submitOnlineSearch('   ');
  equal(searchCalls().length, 0, 'Whitespace-only query does not request results');
});
await test('Search results show source information and queue videos with selected download quality', async () => {
  await submitOnlineSearch('mountain');
  equal(searchTitles(), 'Mountain trail guide|Mountain sunset walk', 'Both platforms render results');
  const first = find('.search-result');
  assert(first.textContent.includes('Trail explorer'), 'Uploader shown');
  assert(first.textContent.includes('YouTube'), 'Platform shown');
  const original = [...first.querySelectorAll('a')].find((link) => link.textContent.trim().startsWith('Open original'));
  assert(original, 'Original video link shown');
  equal(original.href, searchSeed[0].source_url, 'Original link points to search result source');
  await click([...first.querySelectorAll('button')].find((button) => button.textContent.trim() === 'Add to library'));
  const firstEnqueue = calls.filter((call) => call.path === '/api/media' && call.method === 'POST').at(-1);
  equal(JSON.stringify(firstEnqueue.body), JSON.stringify({ urls: [searchSeed[0].source_url], quality: 'best' }), 'First result queued with default quality');
  assert(textButton('Added to queue', '.search-result button').disabled, 'Queued result prevents duplicate clicks');
  await input(find('[aria-label="Search download quality"]'), '720p');
  await click(textButton('Add to library', '.search-result button'));
  const secondEnqueue = calls.filter((call) => call.path === '/api/media' && call.method === 'POST').at(-1);
  equal(JSON.stringify(secondEnqueue.body), JSON.stringify({ urls: [searchSeed[1].source_url], quality: '720p' }), 'Second platform queued with selected quality');
  await navigate('library');
  equal(host.querySelectorAll('.download-row').length, 2, 'Search downloads appear in the existing library queue');
  equal(host.querySelectorAll('.media-card').length, 3, 'Existing saved videos remain in the library');
});
await test('Search results can load a second bounded page in the same session', async () => {
  searchResults = Array.from({ length: 24 }, (_, index) => ({ ...searchSeed[index % 2], id: `result-${index}`, source_url: `https://example.com/video/${index}`, title: `Result ${index + 1}` }));
  searchHasMore = true;
  await submitOnlineSearch('many videos');
  equal(host.querySelectorAll('.search-result').length, 12, 'Initial search page is bounded');
  const initial = searchCalls().at(-1);
  equal(initial.query.limit, '12', 'Initial page requests twelve links');
  assert(initial.query.session, 'Search request includes a stable session identifier');
  await click(textButton('Load more links'));
  equal(host.querySelectorAll('.search-result').length, 24, 'Load more expands the results');
  const expanded = searchCalls().at(-1);
  equal(expanded.query.limit, '24', 'Expanded page requests the larger bound');
  equal(expanded.query.session, initial.query.session, 'Pagination remains in the same repeat-filter session');
});
await test('Fetch all links persists in the route and returns the full configured-page result set', async () => {
  searchResults = Array.from({ length: 35 }, (_, index) => ({ ...customResult, id: `vimeo-${index}`, link_id: 300 + index, source_url: `https://vimeo.com/${1000 + index}`, title: `Vimeo result ${index + 1}` }));
  await click(textButton('Search videos', 'nav button'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  await input(find('input[aria-label="Search online videos"]'), 'all nature links');
  await click(find('input[aria-label="Fetch all links"]'));
  await act(async () => {
    find('form[aria-label="Search online videos"]').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  equal(new URLSearchParams(window.location.hash.split('?')[1]).get('fetch_all'), 'true', 'Fetch-all choice persists in the route');
  equal(searchCalls().at(-1).query.fetch_all, 'true', 'Backend receives fetch-all mode');
  equal(host.querySelectorAll('.search-result').length, 35, 'Every returned configured-page link is displayed');
  equal(host.querySelector('.search-load-more'), null, 'Fetch-all mode does not need the bounded load-more control');
});
await test('Search users can mark repeats and save every visible result for later', async () => {
  await submitOnlineSearch('mountain');
  await click(textButton('Mark repeated', '.search-result button'));
  const repeat = calls.find((call) => call.path === '/api/settings/links/101' && call.method === 'PATCH');
  equal(repeat.body.state, 'silenced', 'Repeat classification is an explicit user request');
  assert(textButton('Marked repeated', '.search-result button').disabled, 'Reviewed link shows its new state');
  await click(textButton('Save all to Watch later'));
  const save = calls.find((call) => call.path === '/api/recommendations/watch-later' && call.method === 'POST');
  equal(save.body.videos.length, 2, 'Every visible result is included in the bulk save');
  equal(save.body.videos[0].source_url, searchSeed[0].source_url, 'Bulk save preserves source URLs');
});
await test('A search-result queue failure is visible and can be retried', async () => {
  await submitOnlineSearch('mountain');
  enqueueFails = true;
  await click(textButton('Add to library', '.search-result button'));
  assert(find('[role="alert"]').textContent.includes('Queue is temporarily unavailable'), 'Queue failure explained');
  equal(searchTitles(), 'Mountain trail guide|Mountain sunset walk', 'Queue failure preserves results');
  assert(!textButton('Add to library', '.search-result button').disabled, 'Failed result is retryable');
  enqueueFails = false;
  await click(textButton('Add to library', '.search-result button'));
  assert(textButton('Added to queue', '.search-result button').disabled, 'Retry queues the result successfully');
});
await test('Search failure retries the same query and displays successful empty results', async () => {
  searchFails = true;
  await submitOnlineSearch('unfindable clip', 'youtube');
  assert(find('[role="alert"]').textContent.includes('Video search is temporarily unavailable'), 'Search failure visible');
  equal(find('input[aria-label="Search online videos"]').value, 'unfindable clip', 'Failed query retained');
  searchFails = false; searchResults = [];
  await click(textButton('Try again'));
  equal(searchCalls().at(-1).query.q, 'unfindable clip', 'Retry retains keywords');
  equal(searchCalls().at(-1).query.source, 'youtube', 'Retry retains platform');
  equal(host.querySelector('[role="alert"]'), null, 'Successful retry clears the error');
  assert(host.textContent.includes('No videos found'), 'Empty results explained');
  equal(host.querySelectorAll('.search-result').length, 0, 'No invented results shown');
});
await test('Partial search results remain usable when one platform reports a warning', async () => {
  searchResults = [searchSeed[0]];
  searchWarnings = [{ source: 'rumble', message: 'Rumble search is currently unavailable' }];
  await submitOnlineSearch('mountain');
  equal(searchTitles(), 'Mountain trail guide', 'Available platform results preserved');
  assert(host.textContent.includes('Rumble search is currently unavailable'), 'Platform warning displayed');
  assert(!textButton('Add to library', '.search-result button').disabled, 'Available result can still be queued');
});
await test('A slower previous search cannot replace the newest query results', async () => {
  let release;
  pendingSearches.set('first query', new Promise((resolve) => { release = resolve; }));
  try {
    await submitOnlineSearch('first query');
    assert(host.querySelector('[role="status"]'), 'Loading status announced');
    searchResults = [{ ...searchSeed[1], title: 'Newest query result' }];
    await submitOnlineSearch('second query', 'rumble');
    equal(searchTitles(), 'Newest query result', 'Newest response displayed');
    await act(async () => release({ query: 'first query', source: 'all', results: [searchSeed[0]], warnings: [] }));
    equal(searchTitles(), 'Newest query result', 'Stale response ignored');
    equal(find('input[aria-label="Search online videos"]').value, 'second query', 'Newest query remains visible');
  } finally {
    await act(async () => release({ query: 'first query', source: 'all', results: [], warnings: [] }));
  }
});
await test('Custom website selection is explicit, retained in the route, and works without AI', async () => {
  const select = find('[aria-label="Search website"]');
  equal([...select.options].map((option) => option.value).join(','), 'all,youtube,vimeo.com', 'Website options include enabled custom websites and exclude disabled ones');
  equal(select.options[0].textContent, 'All enabled websites', 'All scope is clearly named');
  await input(find('input[aria-label="Search online videos"]'), 'mountain & sea');
  await input(select, 'vimeo.com');
  equal(searchCalls().length, 0, 'Typing and choosing a provider do not submit search');
  await submitOnlineSearch('mountain & sea', 'vimeo.com');
  equal(new URLSearchParams(location.hash.split('?')[1]).get('source'), 'vimeo.com', 'Custom id remains in the hash route');
  equal(searchCalls().at(-1).query.source, 'vimeo.com', 'Backend receives the configured custom id');
  equal(find('[aria-label="Search website"]').value, 'vimeo.com', 'Custom selection stays selected on the search page');
  assert(!recommendationSettings.enabled && !recommendationSettings.model_id, 'No model or enabled AI is needed');
  equal(calls.filter((call) => call.path === '/api/recommendations').length, 0, 'Manual search makes no recommendation inference');
  await submitOnlineSearch('mountain & sea', 'all');
  equal(searchCalls().at(-1).query.source, 'all', 'All enabled websites is sent explicitly');
}, () => { recommendationSettings.providers[1].enabled = false; recommendationSettings.providers.push(customProvider); });
await test('Custom deep links and retries preserve the website when keywords change', async () => {
  equal(searchCalls().at(-1).query.source, 'vimeo.com', 'Deep link does not silently become all websites');
  equal(find('[aria-label="Search website"]').value, 'vimeo.com', 'Deep-linked website remains selected');
  searchFails = false;
  await click(textButton('Try again'));
  equal(searchCalls().at(-1).query.source, 'vimeo.com', 'Retry keeps custom website');
  equal(searchCalls().at(-1).query.q, 'birds', 'Retry keeps keyword');
  const count = searchCalls().length;
  await input(find('input[aria-label="Search online videos"]'), 'river birds');
  equal(searchCalls().length, count, 'Editing the keyword does not search early');
  await act(async () => { find('form[aria-label="Search online videos"]').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); window.dispatchEvent(new HashChangeEvent('hashchange')); });
  equal(searchCalls().at(-1).query.source, 'vimeo.com', 'New keyword keeps selected custom website');
  equal(searchCalls().at(-1).query.q, 'river birds', 'New keyword is submitted');
}, () => { recommendationSettings.providers.push(customProvider); searchFails = true; history.replaceState(null, '', '#search?q=birds&source=vimeo.com'); });
let releaseWebsiteSettings;
await test('Direct custom searches wait for website settings before sending a request', async () => {
  equal(searchCalls().length, 0, 'No search uses incomplete default providers');
  assert(host.textContent.includes('Loading search websites'), 'Settings hydration is explained');
  equal(find('[aria-label="Search website"]').value, 'vimeo.com', 'Requested custom id survives initial settings load');
  assert(find('.video-search-form button[type="submit"]').disabled, 'Search is disabled while settings load');
  await act(async () => releaseWebsiteSettings(recommendationSettings));
  equal(searchCalls().length, 1, 'Deep-linked query runs after settings load');
  equal(searchCalls()[0].query.source, 'vimeo.com', 'Hydrated request uses exact custom source');
}, () => { recommendationSettings.providers.push(customProvider); pendingSettings = new Promise((resolve) => { releaseWebsiteSettings = resolve; }); history.replaceState(null, '', '#search?q=birds&source=vimeo.com'); });
await test('Disabled selected websites remain selected and only search after a valid choice is submitted', async () => {
  equal(searchCalls().length, 0, 'Disabled source is never requested');
  equal(find('[aria-label="Search website"]').value, 'vimeo.com', 'Disabled source is not silently changed to all');
  assert(host.textContent.includes('My Vimeo is not enabled'), 'Disabled source is explained by saved name');
  await input(find('[aria-label="Search website"]'), 'youtube');
  equal(searchCalls().length, 0, 'Choosing another website still requires submitting');
  await click(find('.video-search-form button[type="submit"]'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(searchCalls().at(-1).query.source, 'youtube', 'Explicit enabled choice is searched');
}, () => { recommendationSettings.providers.push({ ...customProvider, enabled: false }); history.replaceState(null, '', '#search?q=birds&source=vimeo.com'); });
await test('Removed website deep links provide a settings action without searching another provider', async () => {
  equal(searchCalls().length, 0, 'Unknown or removed source makes no request');
  equal(find('[aria-label="Search website"]').value, 'vimeo.com', 'Removed source route is preserved');
  assert(host.textContent.includes('vimeo.com is not enabled'), 'Removed provider is explained');
  await click(textButton('Website settings'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(location.hash, '#recommendations', 'Settings action opens configured websites');
}, () => { history.replaceState(null, '', '#search?q=birds&source=vimeo.com'); });
await test('No enabled websites offers setup and does not start manual search', async () => {
  equal(searchCalls().length, 0, 'No request without an enabled source');
  assert(host.textContent.includes('Enable at least one website to search for videos'), 'No enabled websites is explained');
  assert(find('.video-search-form button[type="submit"]').disabled, 'Search cannot submit an empty provider scope');
  assert(textButton('Website settings'), 'Website setup action is available');
}, () => { recommendationSettings.providers = recommendationSettings.providers.map((provider) => ({ ...provider, enabled: false })); history.replaceState(null, '', '#search?q=birds&source=all'); });
await test('Website settings failures keep search blocked until settings reload succeeds', async () => {
  equal(searchCalls().length, 0, 'Failed settings do not search default websites');
  assert(find('.video-search-availability').textContent.includes('Website settings unavailable'), 'Settings problem is explained');
  settingsFail = false;
  await click(textButton('Reload websites'));
  equal(searchCalls().at(-1).query.source, 'vimeo.com', 'Settings reload restores exact custom query');
}, () => { settingsFail = true; recommendationSettings.providers.push(customProvider); history.replaceState(null, '', '#search?q=birds&source=vimeo.com'); });
await test('Custom manual results show configured names, unverified links, warnings, and download actions', async () => {
  await submitOnlineSearch('custom nature', 'vimeo.com');
  const result = find('.search-result');
  equal(result.querySelector('.connector-badge').textContent, 'My Vimeo', 'Custom card uses configured display name');
  equal(result.querySelector('.search-unverified').textContent, 'Unverified link', 'Index-only result keeps unverified badge even when recommendation toggle is off');
  assert(result.querySelector('.media-thumb').getAttribute('aria-label').includes('My Vimeo'), 'Original link announces configured website name');
  assert(find('.search-warning strong').textContent.includes('My Vimeo'), 'Custom warnings use configured name');
  equal(host.querySelectorAll('.search-warning').length, 2, 'Different warnings for one provider are retained');
  await click(textButton('Add to library', '.search-result button'));
  equal(calls.filter((call) => call.path === '/api/media' && call.method === 'POST').at(-1).body.urls[0], customResult.source_url, 'Custom result is queued through existing download API');
  equal(location.hash.includes('source=vimeo.com'), true, 'Queueing preserves custom search route');
}, () => { recommendationSettings.providers.push(customProvider); recommendationSettings.allow_unverified_links = false; searchResults = [customResult]; searchWarnings = [{ source: 'vimeo.com', message: 'Some results could not be confirmed.' }, { source: 'vimeo.com', message: 'Recent public matches are included.' }]; });
await test('Search descriptions render as plain text and missing descriptions add no filler', async () => {
  await submitOnlineSearch('nature', 'vimeo.com');
  const cards = [...host.querySelectorAll('.search-result')];
  equal(cards[0].querySelector('.search-result-description').textContent, 'Coastal birds & habitat. <img src=x onerror=alert(1)>', 'Description stays text even if a response contains markup');
  equal(cards[0].querySelector('.search-result-description').children.length, 0, 'Description markup never creates elements');
  equal(cards[0].querySelector('.search-unverified').textContent, 'Unverified link', 'Description does not change verification label');
  equal(cards[1].querySelector('.search-result-description'), null, 'Missing description adds no empty description block');
  equal(cards[2].querySelector('.search-result-description'), null, 'Whitespace-only description adds no filler');
  assert(!cards[0].querySelector('button').disabled, 'Described result remains downloadable');
}, () => { recommendationSettings.providers.push(customProvider); searchResults = [{ ...customResult, description: 'Coastal birds & habitat. <img src=x onerror=alert(1)>' }, { ...customResult, id: 'vimeo-124', source_url: 'https://vimeo.com/124' }, { ...customResult, id: 'vimeo-125', source_url: 'https://vimeo.com/125', description: '  ' }]; });
await test('Removing a provider aborts its pending manual search and rejects a late result', async () => {
  await act(async () => root.unmount());
  root = createRoot(host);
  let release;
  pendingSearches.set('pending custom', new Promise((resolve) => { release = resolve; }));
  await act(async () => root.render(<RecommendationProvider><ProviderSettingsDriver /><SearchPage query="pending custom" source="vimeo.com" navigate={() => {}} /></RecommendationProvider>));
  const pendingCall = searchCalls().at(-1);
  assert(pendingCall, 'Custom request starts while provider is enabled');
  assert(host.textContent.includes('Searching My Vimeo'), 'Loading names the configured website');
  await click(textButton('Remove test website'));
  assert(pendingCall.signal.aborted, 'Provider mutation aborts the pending request');
  await act(async () => release({ query: 'pending custom', source: 'vimeo.com', results: [customResult], warnings: [] }));
  equal(host.querySelector('.search-result'), null, 'Late removed-provider results are ignored');
  equal(searchCalls().length, 1, 'Removed provider does not fall back to another search');
  assert(host.textContent.includes('vimeo.com is not enabled'), 'Provider removal offers a valid selection path');
}, () => { recommendationSettings.providers.push(customProvider); });
await test('AI preference changes do not abort or repeat a manual search', async () => {
  await act(async () => root.unmount());
  root = createRoot(host);
  let release;
  pendingSearches.set('pending custom', new Promise((resolve) => { release = resolve; }));
  await act(async () => root.render(<RecommendationProvider><ProviderSettingsDriver /><SearchPage query="pending custom" source="vimeo.com" navigate={() => {}} /></RecommendationProvider>));
  const pendingCall = searchCalls().at(-1);
  await click(textButton('Change AI preference'));
  assert(!pendingCall.signal.aborted, 'An unrelated AI setting does not abort manual search');
  equal(searchCalls().length, 1, 'AI settings save does not repeat manual request');
  await act(async () => release({ query: 'pending custom', source: 'vimeo.com', results: [customResult], warnings: [] }));
  equal(searchTitles(), customResult.title, 'Manual result survives an AI preference change');
  await click(textButton('Change AI preference'));
  equal(searchCalls().length, 1, 'Completed manual search is not repeated when AI is turned off');
  equal(searchTitles(), customResult.title, 'Completed results stay visible with AI off');
}, () => { recommendationSettings.providers.push(customProvider); });
await test('Video topics word cloud searches saved videos and unused tabs are absent', async () => {
  const navLabels = [...host.querySelectorAll('nav button')].map((button) => button.textContent.trim());
  assert(!navLabels.includes('Liked videos'), 'Liked videos tab removed');
  assert(!navLabels.includes('Connectors'), 'Connectors tab removed');
  await click(textButton('Video topics', 'nav button'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(keywordTitles(), 'Mountain morning|City at night|Ocean sounds', 'All tagged videos shown');
  equal(host.querySelectorAll('.keyword-cloud button').length, 7, 'Word cloud shows generated topics');
  const city = [...host.querySelectorAll('.keyword-cloud button')].find((button) => button.querySelector('span')?.textContent === 'city');
  await click(city);
  equal(keywordTitles(), 'City at night', 'Clicking a cloud word filters videos');
  await input(find('[aria-label="Search videos by keyword"]'), 'ocean');
  await act(async () => find('.keyword-search').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  equal(keywordTitles(), 'Ocean sounds', 'Typed keyword search filters videos');
});
await test('Player mode preference persists and opens the selected custom Watch player', async () => {
  await click(textButton('Watch', '.player-mode-switch button'));
  equal(localStorage.getItem('clipfeed.playerMode'), 'watch', 'Watch preference persisted');
  await click(find('.media-thumb'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(window.location.hash, '#watch/youtube-one', 'Card keeps saved video identity');
  equal(find('video').controls, false, 'Watch has no native video controls');
  assert(find('[aria-label="Seek video"]'), 'Watch custom seek control');
});
await test('URL imports deduplicate, route supported URLs, and preserve unsupported URLs', async () => {
  const youtube = 'https://youtu.be/example';
  const rumble = 'https://rumble.com/v-test.html';
  const unsupported = 'https://unsupported.invalid/video';
  await input(find('#import-urls'), `${youtube}\n${rumble}\n${youtube}\n${unsupported}`);
  await wait(350);
  equal(host.querySelectorAll('.url-chip').length, 3, 'Duplicate URL removed');
  await input(find('[aria-label="Download quality"]'), '720p');
  await click(find('.import-panel button[type="submit"]'));
  const enqueue = calls.find((call) => call.path === '/api/media' && call.method === 'POST');
  equal(JSON.stringify(enqueue.body), JSON.stringify({ urls: [youtube, rumble], quality: '720p' }), 'Only supported URLs and chosen quality submitted');
  equal(find('#import-urls').value, unsupported, 'Unsupported URL kept for correction');
  equal(host.querySelectorAll('.download-row').length, 2, 'Queued downloads visible');
  assert(find('.success-banner').textContent.includes('2 videos added'), 'Queue success shown');
});
await test('Queue submission failure keeps URLs and existing library data', async () => {
  enqueueFails = true;
  await input(find('#import-urls'), 'https://youtu.be/example');
  await wait(350);
  await click(find('.import-panel button[type="submit"]'));
  equal(find('#import-urls').value, 'https://youtu.be/example', 'Failed URL preserved');
  equal(host.querySelectorAll('.media-card').length, 3, 'Existing collection preserved');
  assert(find('[role="alert"]').textContent.includes('Queue is temporarily unavailable'), 'Submission error visible');
  equal(host.querySelector('.success-banner'), null, 'No false success banner');
});
await test('Failed library deletion preserves the saved video', async () => {
  deleteFails = true;
  await click(find('.media-card [aria-label="Delete video"]'));
  equal(host.querySelectorAll('.media-card').length, 3, 'Failed deletion retains all items');
  assert(find('[role="alert"]').textContent.includes('Delete failed on server'), 'Deletion error visible');
});
await test('Refresh failures preserve the previous collection and retry recovers', async () => {
  loadFails = true;
  await wait(3100);
  equal(host.querySelectorAll('.media-card').length, 3, 'Existing data survives refresh failure');
  assert(find('[role="alert"]').textContent.includes('Library server unavailable'), 'Connection error visible');
  loadFails = false;
  await click(textButton('Try again'));
  equal(host.querySelector('[role="alert"]'), null, 'Retry clears connection error');
  equal(host.querySelectorAll('.media-card').length, 3, 'Retry restores server collection');
});
const passed = results.filter((result) => result.passed).length;
document.body.dataset.testStatus = passed === results.length ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${passed}/${results.length} tests passed`;
