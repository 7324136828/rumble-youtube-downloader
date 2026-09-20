import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const report = [];
const warning = 'Automatic conversion is off. This format may not play in your browser; download the original file to use an external player.';
const video = { id: 'original', title: 'Original video', connector: 'youtube', source_url: 'https://www.youtube.com/watch?v=abcdefghijk', status: 'ready', duration: 120, created_at: '2026-09-20', stream_url: '/tests/player.fixture.webm', download_url: '/tests/player.fixture.webm', playback_warning: warning };
let root, settings, calls, media, loadFailure, saveFailure, recommendationsEnabled;
Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value() { return Promise.resolve(); } });
Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value() {} });
window.fetch = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : undefined;
  calls.push({ path: url.pathname, method, body });
  let value, error;
  if (url.pathname === '/api/settings/downloads') {
    if (method === 'PATCH') {
      if (saveFailure) error = 'Could not save download preferences';
      else settings = { ...settings, ...body };
    } else if (loadFailure) error = 'Download settings unavailable';
    value = { ...settings };
  } else if (url.pathname === '/api/recommendations/settings') value = { enabled: recommendationsEnabled, model_id: 'test-model', seed_keywords: [], revision: 1 };
  else if (url.pathname === '/api/recommendations') value = { status: 'ready', items: [{ ...video, media_id: null }], keywords: [], warnings: [] };
  else if (url.pathname === '/api/connectors') value = [];
  else if (url.pathname === '/api/watch-history') value = { recorded: true };
  else if (url.pathname === '/api/media') {
    if (method === 'POST') {
      media = [{ ...video, status: 'processing', progress: 92, stage: 'converting', playback_warning: null }];
      value = media;
    } else value = url.searchParams.get('status') ? media.filter((item) => item.status === url.searchParams.get('status')) : media;
  } else if (url.pathname.startsWith('/api/media/')) value = media.find((item) => item.id === url.pathname.split('/').at(-1));
  else throw new Error(`Unexpected request: ${method} ${path}`);
  return { ok: !error, status: error ? 500 : 200, json: async () => error ? { detail: error } : value };
};
const assert = (value, message) => { if (!value) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, received ${actual}`);
const find = (selector) => { const item = host.querySelector(selector); assert(item, `Missing ${selector}`); return item; };
const button = (text) => { const item = [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === text); assert(item, `Missing button ${text}`); return item; };
const click = (item) => act(async () => item.click());
const navigate = (route) => act(async () => { location.hash = route; window.dispatchEvent(new HashChangeEvent('hashchange')); });
const convert = () => find('#download-convert-for-browser');
const thumbnails = () => find('#download-generate-thumbnails');
async function mount(route = 'settings') {
  history.replaceState(null, '', `#${route}`);
  root = createRoot(host);
  await act(async () => root.render(<App />));
}
async function test(name, body) {
  root = null; calls = []; media = []; loadFailure = saveFailure = recommendationsEnabled = false;
  settings = { convert_for_browser: false, generate_thumbnails: true };
  localStorage.clear();
  try { await body(); report.push({ name, passed: true }); }
  catch (error) { report.push({ name, passed: false, message: error.message }); }
  finally { if (root) await act(async () => root.unmount()); host.replaceChildren(); }
  document.getElementById('results').textContent = report.map((item) => `${item.passed ? 'PASS' : 'FAIL'} ${item.name}${item.message ? ': ' + item.message : ''}`).join('\n');
}

await test('MP4 conversion is off by default and explains direct WebM playback', async () => {
  await mount();
  assert(!convert().checked && thumbnails().checked, 'Current defaults shown');
  assert(host.textContent.includes('Convert downloads to MP4'), 'Explicit MP4 option shown');
  assert(host.textContent.includes('Off by default'), 'Conversion default explained');
  assert(host.textContent.includes('WebM plays directly in Watch and Swipe'), 'Direct playback explained');
  assert(host.textContent.includes('new downloads'), 'New-download scope explained');
  assert(button('Save settings').disabled, 'Unchanged settings cannot be saved');
  equal(calls.filter((call) => call.method === 'PATCH').length, 0, 'Loading settings does not enable conversion');
});
await test('Saving thumbnail preferences keeps MP4 conversion disabled across navigation', async () => {
  await mount();
  await click(thumbnails());
  equal(calls.filter((call) => call.method === 'PATCH').length, 0, 'Draft requires explicit save');
  await click(button('Save settings'));
  equal(settings.convert_for_browser, false, 'Unrelated save keeps conversion disabled');
  equal(settings.generate_thumbnails, false, 'Thumbnail generation disabled');
  equal(calls.find((call) => call.method === 'PATCH').body.convert_for_browser, false, 'Request preserves conversion choice');
  await navigate('downloads');
  await click(button('Settings'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  assert(!convert().checked && !thumbnails().checked, 'Saved values restored');
});
await test('Explicit MP4 opt-in and later opt-out persist across navigation', async () => {
  await mount();
  await click(convert());
  equal(settings.convert_for_browser, false, 'Opt-in draft does not persist before save');
  await click(button('Save settings'));
  equal(settings.convert_for_browser, true, 'Explicit opt-in saved');
  await navigate('downloads');
  await navigate('settings');
  assert(convert().checked, 'Opt-in restored');
  await click(convert());
  await click(button('Save settings'));
  equal(settings.convert_for_browser, false, 'Opt-out saved');
  await navigate('downloads');
  await navigate('settings');
  assert(!convert().checked, 'Opt-out restored');
});
await test('Settings load failures offer retry without allowing blind saves', async () => {
  loadFailure = true;
  await mount();
  assert(host.textContent.includes('Download settings unavailable'), 'Load error shown');
  const saveButton = [...host.querySelectorAll('button')].find((item) => item.textContent.trim() === 'Save settings');
  assert(!saveButton || saveButton.disabled, 'No save before loading');
  loadFailure = false;
  await click(button('Retry loading'));
  assert(!convert().checked && !convert().disabled, 'Retry recovered with conversion still disabled');
});
await test('Save failure preserves draft and supports retry', async () => {
  await mount();
  await click(convert());
  saveFailure = true;
  await click(button('Save settings'));
  assert(host.textContent.includes('Could not save download preferences'), 'Save error shown');
  assert(convert().checked, 'Opt-in draft retained');
  assert(!settings.convert_for_browser, 'Failed save leaves conversion disabled');
  saveFailure = false;
  await click(button('Save settings'));
  assert(settings.convert_for_browser, 'Retry saved explicit opt-in');
});
await test('Processing stages have meaningful labels and no frozen percentage', async () => {
  media = ['checking', 'converting', 'thumbnail', 'merging', 'processing'].map((stage) => ({ ...video, id: stage, status: 'processing', stage, progress: 92 }));
  media.push({ ...video, id: 'still-downloading', status: 'downloading', stage: 'downloading', progress: 45 });
  await mount('downloads');
  assert(host.textContent.includes('Converting for browser playback'), 'Conversion explained');
  assert(!host.textContent.includes('92%'), 'No fixed processing percent');
  assert(host.textContent.includes('45%'), 'Actual download percent retained');
  const bars = [...host.querySelectorAll('.download-row [role="progressbar"]')];
  equal(bars.length, 6, 'Each download has progress status');
  equal(bars.filter((bar) => !bar.hasAttribute('aria-valuenow')).length, 5, 'Processing is indeterminate');
});
await test('Original-file playback warning appears in both player views', async () => {
  media = [video];
  await mount('watch/original');
  assert(host.textContent.includes(warning), 'Watch warns about original format');
  await navigate('feed/original');
  assert(host.textContent.includes(warning), 'Feed warns about original format');
});
await test('Recommendation download button shows conversion instead of frozen download percentage', async () => {
  recommendationsEnabled = true;
  await mount('feed');
  await click(button('Download & play'));
  assert(find('.recommendation-item').textContent.includes('Converting for browser playback'), 'Recommendation conversion stage shown');
  assert(!find('.recommendation-item').textContent.includes('92%'), 'No misleading percentage');
});

const passed = report.filter((item) => item.passed).length;
document.body.dataset.testStatus = passed === report.length ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${passed}/${report.length} passed`;
