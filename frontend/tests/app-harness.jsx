import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';

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
  { id: 'youtube-search-one', title: 'Mountain trail guide', source_url: 'https://www.youtube.com/watch?v=search-one', connector: 'youtube', uploader: 'Trail explorer', duration: 125, thumbnail_url: null },
  { id: 'rumble-search-two', title: 'Mountain sunset walk', source_url: 'https://rumble.com/v-search-two.html', connector: 'rumble', uploader: 'Sunset channel', duration: 240, thumbnail_url: null },
];
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
let pendingSearches;
window.confirm = () => true;
window.fetch = async (path, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : undefined;
  const method = options.method || 'GET';
  const url = new URL(path, window.location.origin);
  calls.push({ path: url.pathname, method, body, query: Object.fromEntries(url.searchParams) });
  let response;
  let error;
  if (url.pathname === '/api/recommendations/settings') response = { enabled: false, model_id: null, seed_keywords: [], revision: 0 };
  else if (url.pathname === '/api/watch-history') response = { recorded: true };
  else if (url.pathname === '/api/connectors') response = connectors;
  else if (url.pathname === '/api/search') {
    if (searchFails) error = 'Video search is temporarily unavailable';
    response = pendingSearches.has(url.searchParams.get('q'))
      ? await pendingSearches.get(url.searchParams.get('q'))
      : { query: url.searchParams.get('q'), source: url.searchParams.get('source'), results: searchResults, warnings: searchWarnings };
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
const searchCalls = () => calls.filter((call) => call.path === '/api/search');
async function input(element, value) {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}
async function submitOnlineSearch(query, source = 'all') {
  await input(find('[aria-label="Search YouTube and Rumble"]'), query);
  await input(find('[aria-label="Search platform"]'), source);
  await act(async () => {
    find('form[aria-label="Search online videos"]').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}
async function test(name, body) {
  items = seed.map((item) => ({ ...item })); calls = [];
  loadFails = enqueueFails = deleteFails = resolveFails = searchFails = false;
  searchResults = searchSeed.map((item) => ({ ...item })); searchWarnings = []; pendingSearches = new Map();
  localStorage.clear(); sessionStorage.clear();
  history.replaceState(null, '', '#library');
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
  equal(find('[aria-label="Search YouTube and Rumble"]').value, query, 'Search input reflects the route');
  equal(find('[aria-label="Search platform"]').value, 'rumble', 'Platform input reflects the route');
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
  equal(find('[aria-label="Search YouTube and Rumble"]').value, 'unfindable clip', 'Failed query retained');
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
    equal(find('[aria-label="Search YouTube and Rumble"]').value, 'second query', 'Newest query remains visible');
  } finally {
    await act(async () => release({ query: 'first query', source: 'all', results: [], warnings: [] }));
  }
});
await test('Likes persist across screens and unlike removes the liked card', async () => {
  await click(find('.media-card [aria-label="Like video"]'));
  equal(localStorage.getItem('clipfeed.likes'), '["youtube-one"]', 'Liked ID persisted');
  await navigate('liked');
  equal(titles(), 'Mountain morning', 'Liked screen shows only liked video');
  await click(find('[aria-label="Unlike video"]'));
  equal(host.querySelectorAll('.media-card').length, 0, 'Unlike updates liked screen');
  equal(localStorage.getItem('clipfeed.likes'), '[]', 'Unlike persisted');
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
await test('Connector checker lists connectors and distinguishes unsupported links', async () => {
  await navigate('connectors');
  equal(host.querySelectorAll('.connector-card').length, 3, 'Connectors listed');
  await input(find('#connector-url'), 'https://rumble.com/v-test.html');
  await click(textButton('Check link'));
  assert(find('.connection-result').textContent.includes('Rumble connector'), 'Connector resolved');
  await input(find('#connector-url'), 'https://unsupported.invalid/video');
  await click(textButton('Check link'));
  assert(find('.connection-result').textContent.includes('No connector accepts'), 'Unsupported link explained');
  resolveFails = true;
  await input(find('#connector-url'), 'https://youtu.be/example');
  await click(textButton('Check link'));
  assert(find('[role="alert"]').textContent.includes('Connector service unavailable'), 'Resolver failure shown');
  equal(find('#connector-url').value, 'https://youtu.be/example', 'Failed check preserves input');
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
