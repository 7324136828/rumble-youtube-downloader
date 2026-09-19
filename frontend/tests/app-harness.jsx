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
let root;
let items;
let calls;
let loadFails;
let enqueueFails;
let deleteFails;
let resolveFails;
window.confirm = () => true;
window.fetch = async (path, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : undefined;
  const method = options.method || 'GET';
  const url = new URL(path, window.location.origin);
  calls.push({ path: url.pathname, method, body });
  let response;
  let error;
  if (url.pathname === '/api/connectors') response = connectors;
  else if (url.pathname === '/api/resolve') {
    if (resolveFails) error = 'Connector service unavailable';
    response = body.urls.map((url) => ({ url, connector: url.includes('unsupported.invalid') ? null : connectors.find((item) => item.id === (url.includes('youtu') ? 'youtube' : url.includes('rumble') ? 'rumble' : 'generic')) }));
  } else if (url.pathname === '/api/media' && method === 'POST') {
    if (enqueueFails) error = 'Queue is temporarily unavailable';
    response = body.urls.map((url, index) => ({ id: `queued-${index}`, source_url: url, title: `Queued video ${index + 1}`, connector: url.includes('youtu') ? 'youtube' : 'rumble', status: 'queued', progress: 0, quality: body.quality }));
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
async function input(element, value) {
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, value);
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}
async function test(name, body) {
  items = seed.map((item) => ({ ...item })); calls = [];
  loadFails = enqueueFails = deleteFails = resolveFails = false;
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
