import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const report = [];
const suggestion = { id: 'youtube:abcdefghijk', title: 'River wildlife guide', source_url: 'https://www.youtube.com/watch?v=abcdefghijk', connector: 'youtube', uploader: 'Nature channel', duration: 123, thumbnail_url: null };
let root, settings, models, calls, response, media, failure, modelsFailure, downloadFailure, pending, pendingPatch;
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
  } else if (url.pathname === '/api/recommendations') {
    if (failure) error = 'Model could not produce a playlist';
    value = pending ? await pending : response;
  } else if (url.pathname === '/api/media') {
    if (method === 'POST') {
      const queued = { ...suggestion, id: 'downloaded-video', status: 'queued', source_url: body.urls[0], quality: body.quality };
      media = [{ ...queued, status: downloadFailure ? 'failed' : 'ready', error_message: downloadFailure, stream_url: '/tests/player.fixture.webm' }, ...media];
      value = [queued];
    } else value = media;
  } else if (url.pathname.startsWith('/api/media/')) value = media.find((item) => item.id === url.pathname.split('/').at(-1));
  else if (url.pathname === '/api/watch-history') value = { recorded: true };
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
async function test(name, body) {
  settings = { enabled: false, model_id: 'my-config', seed_keywords: [], revision: 0 };
  models = [{ id: 'my-config', name: 'My config' }, { id: 'second-model', name: 'Second model' }];
  calls = []; media = []; failure = modelsFailure = false; downloadFailure = null; pending = pendingPatch = null; root = null;
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
await test('Cold start explains how to seed recommendations', async () => {
  settings.enabled = true;
  response = { status: 'needs_history', items: [], keywords: [], warnings: [] };
  await mount();
  assert(find('.recommendation-panel').textContent.includes('Watch a video or add a few interests'), 'Cold start explained');
  await click(button('Add your interests'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(location.hash, '#recommendations', 'Interests link opens settings');
});
await test('Download and play queues the exact result and opens ready media', async () => {
  settings.enabled = true;
  await mount();
  await click(button('Download & play'));
  await act(async () => window.dispatchEvent(new HashChangeEvent('hashchange')));
  equal(posts('/api/media').length, 1, 'Exactly one download queued');
  equal(posts('/api/media')[0].body.urls[0], suggestion.source_url, 'Verified URL used');
  equal(location.hash, '#feed/downloaded-video', 'Ready result opened in feed');
});
await test('A failed recommendation download displays the backend reason and can be retried', async () => {
  settings.enabled = true;
  downloadFailure = 'The source temporarily refused the download. Please try again.';
  await mount();
  await click(button('Download & play'));
  equal(find('.recommendation-download-error').textContent, downloadFailure, 'Backend error_message shown');
  assert(!button('Retry download & play').disabled, 'Failed download exposes an enabled retry');
  equal(location.hash, '#feed', 'A failed download does not start playback');
  downloadFailure = null;
  await click(button('Retry download & play'));
  equal(posts('/api/media').length, 2, 'Retry queues a new download');
  equal(location.hash, '#feed/downloaded-video', 'Successful retry opens ready media');
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

const passed = report.filter((item) => item.passed).length;
document.body.dataset.testStatus = passed === report.length ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${passed}/${report.length} passed`;
