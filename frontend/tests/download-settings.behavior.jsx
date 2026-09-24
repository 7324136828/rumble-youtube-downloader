import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const report = [];
const warning = 'Automatic conversion is off. This format may not play in your browser; download the original file to use an external player.';
const video = { id: 'original', title: 'Original video', connector: 'youtube', source_url: 'https://www.youtube.com/watch?v=abcdefghijk', status: 'ready', duration: 120, created_at: '2026-09-20', stream_url: '/tests/player.fixture.webm', download_url: '/tests/player.fixture.webm', playback_warning: warning };
let root, settings, linkSettings, linkItems, calls, media, loadFailure, saveFailure, recommendationsEnabled, retentionSaveFailure;
Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value() { return Promise.resolve(); } });
Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value() {} });
window.fetch = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : undefined;
  calls.push({ path: url.pathname, method, body });
  let value, error;
  if (url.pathname === '/api/settings/links') {
    if (method === 'PATCH') linkSettings = { ...linkSettings, ...body };
    value = { ...linkSettings, items: linkItems.filter((item) => !url.searchParams.get('state') || url.searchParams.get('state') === 'all' || item.state === url.searchParams.get('state')), total: linkItems.length, limit: 200, offset: 0 };
  } else if (url.pathname.startsWith('/api/settings/links/') && method === 'PATCH') {
    const id = Number(url.pathname.split('/').at(-1));
    linkItems = linkItems.map((item) => item.id === id ? { ...item, state: body.state } : item);
    value = linkItems.find((item) => item.id === id);
  } else if (url.pathname.startsWith('/api/settings/links/') && method === 'DELETE') {
    const id = Number(url.pathname.split('/').at(-1));
    linkItems = linkItems.filter((item) => item.id !== id);
    value = { deleted: true, id };
  } else if (url.pathname === '/api/settings/downloads') {
    if (method === 'PATCH') {
      if (saveFailure) error = 'Could not save download preferences';
      else {
        settings = { ...settings, ...body };
        if (body.retention_days !== undefined) {
          settings.retention_days = body.retention_days < 0 ? -1 : body.retention_days;
          settings.auto_delete_enabled = settings.retention_days > 0;
        }
      }
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
  } else if (url.pathname.startsWith('/api/media/') && url.pathname.endsWith('/retention') && method === 'PATCH') {
    const id = decodeURIComponent(url.pathname.slice('/api/media/'.length, -'/retention'.length));
    const item = media.find((entry) => entry.id === id);
    if (!item) throw new Error(`Unknown video retention request: ${path}`);
    if (retentionSaveFailure) error = 'Could not save this video expiration';
    else {
      const days = body.retention_days < 0 ? null : body.retention_days;
      value = { ...item, retention_days: days, retention_override: true,
        expires_at: days && item.status === 'ready' ? new Date(new Date(item.completed_at || item.created_at).getTime() + days * 86400000).toISOString() : null };
      media = media.map((entry) => entry.id === id ? value : entry);
    }
  } else if (url.pathname.startsWith('/api/media/')) value = media.find((item) => item.id === url.pathname.split('/').at(-1));
  else throw new Error(`Unexpected request: ${method} ${path}`);
  return { ok: !error, status: error ? 500 : 200, json: async () => error ? { detail: error } : value };
};
const assert = (value, message) => { if (!value) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, received ${actual}`);
const find = (selector) => { const item = host.querySelector(selector); assert(item, `Missing ${selector}`); return item; };
const button = (text) => { const item = [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === text); assert(item, `Missing button ${text}`); return item; };
const click = (item) => act(async () => item.click());
const input = (item, value) => act(async () => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(item, value);
  item.dispatchEvent(new Event('input', { bubbles: true }));
});
const navigate = (route) => act(async () => { location.hash = route; window.dispatchEvent(new HashChangeEvent('hashchange')); });
const convert = () => find('#download-convert-for-browser');
const thumbnails = () => find('#download-generate-thumbnails');
const videoRetentionRequests = () => calls.filter((call) => call.method === 'PATCH' && call.path.endsWith('/retention'));
const dialog = () => { const item = document.querySelector('[role="dialog"]'); assert(item, 'Video expiration dialog opens'); return item; };
const dialogButton = (text) => { const item = [...dialog().querySelectorAll('button')].find((el) => el.textContent.trim() === text); assert(item, `Missing dialog button ${text}`); return item; };
const videoDays = () => { const item = dialog().querySelector('#video-retention-modal-days'); assert(item, 'Video-specific days input exists'); return item; };
const expirationButton = (item) => find(`button[aria-label="Expiration settings for ${item.title || item.file_name || item.source_url}"]`);
const retainedVideo = (overrides = {}) => {
  const completedAt = new Date(Date.now() - 86400000).toISOString();
  return { ...video, retention_days: 7, retention_override: false, completed_at: completedAt,
    expires_at: new Date(new Date(completedAt).getTime() + 7 * 86400000).toISOString(), ...overrides };
};
async function mount(route = 'settings') {
  history.replaceState(null, '', `#${route}`);
  root = createRoot(host);
  await act(async () => root.render(<App />));
}
async function test(name, body) {
  root = null; calls = []; media = []; loadFailure = saveFailure = recommendationsEnabled = retentionSaveFailure = false;
  settings = { convert_for_browser: false, generate_thumbnails: true, auto_delete_enabled: true, retention_days: 7, cookie_browser: '', cookie_browser_profile: '', cookie_file: '' };
  linkSettings = { hide_repeated_links: true };
  linkItems = [{ id: 1, canonical_id: 'youtube:repeat', source_url: 'https://youtube.com/watch?v=repeat', title: 'Repeated video', provider: 'youtube', state: 'active', seen_count: 2, first_seen_at: '2026-09-20', last_seen_at: '2026-09-21' }];
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
  assert(host.textContent.includes('Days to keep each video'), 'Retention setting shown');
  equal(find('#download-retention-days').value, '7', 'Seven-day default shown');
  assert(host.textContent.includes('checked hourly'), 'Hourly cleanup schedule is explained');
  assert(button('Save settings').disabled, 'Unchanged settings cannot be saved');
  equal(calls.filter((call) => call.method === 'PATCH').length, 0, 'Loading settings does not enable conversion');
});
await test('Browser-cookie authentication is explicit and persists only browser selection', async () => {
  await mount();
  const browser = find('#download-cookie-browser');
  equal(browser.value, '', 'Cookie access is off by default');
  assert(host.textContent.includes('cookie values are never stored'), 'Cookie storage boundary is explained');
  await act(async () => { browser.value = 'edge'; browser.dispatchEvent(new Event('change', { bubbles: true })); });
  const profile = find('#download-cookie-profile');
  await input(profile, 'Profile 2');
  await click(button('Save settings'));
  const request = calls.filter((call) => call.path === '/api/settings/downloads' && call.method === 'PATCH').at(-1);
  equal(request.body.cookie_browser, 'edge', 'Selected browser is persisted');
  equal(request.body.cookie_browser_profile, 'Profile 2', 'Optional profile is persisted');
  assert(!JSON.stringify(request.body).includes('cookie_value'), 'No cookie value is sent to ClipFeed');
});
await test('An exported cookie file can be saved and cleared without losing the Edge profile', async () => {
  settings.cookie_browser = 'edge';
  settings.cookie_browser_profile = 'Profile 2';
  await mount();
  const cookiePath = 'C:\\Private\\youtube-cookies.txt';
  const patches = () => calls.filter((call) => call.path === '/api/settings/downloads' && call.method === 'PATCH');
  equal(find('#download-cookie-file').value, '', 'File override starts empty');
  assert(host.textContent.includes('Startup boost'), 'Background Edge locks are explained');
  assert(host.textContent.includes('errors can say "Chrome"'), 'Edge error wording is explained');
  assert(host.textContent.includes('file takes priority over browser cookies'), 'File priority is explained');
  await input(find('#download-cookie-file'), `  ${cookiePath}  `);
  equal(patches().length, 0, 'Entering a private file path does not save it automatically');
  await click(button('Save settings'));
  equal(patches().at(-1).body.cookie_file, cookiePath, 'Only the trimmed file path is saved');
  equal(settings.cookie_browser, 'edge', 'Browser choice is retained');
  equal(settings.cookie_browser_profile, 'Profile 2', 'Browser profile is retained');
  await navigate('downloads');
  await navigate('settings');
  equal(find('#download-cookie-file').value, cookiePath, 'Saved file path is restored');
  equal(find('#download-cookie-browser').value, 'edge', 'Original browser choice is restored');
  equal(find('#download-cookie-profile').value, 'Profile 2', 'Original profile is restored');
  await input(find('#download-cookie-file'), '');
  equal(patches().length, 1, 'Clearing the field is also a draft until saved');
  await click(button('Save settings'));
  equal(patches().at(-1).body.cookie_file, '', 'Empty path removes the override');
  equal(patches().at(-1).body.cookie_browser, 'edge', 'Clearing the override preserves browser authentication');
  equal(patches().at(-1).body.cookie_browser_profile, 'Profile 2', 'Clearing the override preserves the profile');
});
await test('Older settings without a cookie file or profile can still be saved', async () => {
  settings.cookie_browser = 'edge';
  delete settings.cookie_browser_profile;
  delete settings.cookie_file;
  await mount();
  equal(find('#download-cookie-file').value, '', 'Missing file value displays empty');
  equal(find('#download-cookie-profile').value, '', 'Missing profile displays empty');
  assert(button('Save settings').disabled, 'Defaults do not create a phantom unsaved change');
  await click(thumbnails());
  await click(button('Save settings'));
  equal(settings.generate_thumbnails, false, 'Unrelated preference saves successfully');
  equal(settings.cookie_browser, 'edge', 'Existing browser selection is retained');
  equal(settings.cookie_browser_profile, '', 'Missing profile is normalized');
  equal(settings.cookie_file, '', 'Missing file value is normalized');
});
await test('Link settings leave repeat classification to human review', async () => {
  await mount();
  assert(find('#hide-repeated-links').checked, 'Repeated links are hidden by default');
  await click(find('#hide-repeated-links'));
  equal(linkSettings.hide_repeated_links, false, 'Repeat filter can be disabled');
  await click(button('Manage recorded links'));
  assert(dialog().textContent.includes('does not decide') || host.textContent.includes('does not decide'), 'Settings explain that repeat decisions are manual');
  assert(dialog().textContent.includes('Repeated video'), 'Recorded link is shown in the popup');
  await click(dialogButton('Mark repeated'));
  equal(linkItems[0].state, 'silenced', 'Human review explicitly marks a repeated link');
  await click(dialogButton('Allow link'));
  equal(linkItems[0].state, 'allowed', 'Human review can allow a marked link again');
  await click(dialogButton('Clear marking'));
  equal(linkItems[0].state, 'active', 'Human review can return a link to unreviewed');
  await click(dialogButton('Delete record'));
  equal(linkItems.length, 0, 'Human review can delete a recorded link');
  assert(dialog().textContent.includes('No links in this view'), 'Deleted record is removed from the review window');
  await click(dialogButton('Done'));
  equal(document.querySelector('[role="dialog"]'), null, 'Done closes link review');
});
await test('Expiration settings open in a modal and negative days keep videos indefinitely', async () => {
  await mount('downloads');
  await click(button('Expiration settings'));
  assert(find('[role="dialog"]'), 'Expiration modal opens over the download list');
  equal(find('#retention-modal-days').value, '7', 'Current retention is loaded');
  assert(host.textContent.includes('download finished'), 'Completion-date calculation is explained');
  await input(find('#retention-modal-days'), '-5');
  await click(button('Save expiration'));
  const request = calls.filter((call) => call.path === '/api/settings/downloads' && call.method === 'PATCH').at(-1);
  equal(request.body.retention_days, -5, 'Negative value is sent without clamping');
  equal(settings.retention_days, -1, 'Backend-normalized indefinite policy is retained');
  assert(!settings.auto_delete_enabled, 'Negative retention disables expiration');
  equal(host.querySelector('[role="dialog"]'), null, 'Successful save closes the modal');
  await click(button('Expiration settings'));
  equal(find('#retention-modal-days').value, '-1', 'Reopening displays indefinite retention');
});
await test('A completed video saves its own expiration without changing siblings or global settings', async () => {
  const selected = retainedVideo({ id: 'video:one', title: 'Selected video' });
  const sibling = retainedVideo({ id: 'sibling', title: 'Sibling video' });
  media = [selected, sibling];
  await mount('downloads');
  const originalSettings = JSON.stringify(settings);
  const siblingLabel = expirationButton(sibling).closest('.media-card').textContent;
  const initialLoads = calls.filter((call) => call.path === '/api/media' && call.method === 'GET').length;
  await click(expirationButton(selected));
  equal(document.querySelectorAll('[role="dialog"]').length, 1, 'Only the selected video has an open dialog');
  assert(dialog().textContent.includes('Video expiration') && dialog().textContent.includes(selected.title), 'Dialog identifies the selected video');
  equal(videoDays().value, '7', 'Current video retention is loaded');
  await input(videoDays(), '14');
  await click(dialogButton('Save expiration'));
  equal(document.querySelector('[role="dialog"]'), null, 'Successful save closes the dialog');
  const request = videoRetentionRequests().at(-1);
  equal(request.path, `/api/media/${encodeURIComponent(selected.id)}/retention`, 'Only the selected video is patched with an encoded identifier');
  equal(request.body.retention_days, 14, 'Requested video expiration is sent');
  equal(media[0].retention_days, 14, 'Video-specific retention persists');
  assert(media[0].retention_override, 'Video receives a retention override');
  equal(media[0].expires_at, new Date(new Date(selected.completed_at).getTime() + 14 * 86400000).toISOString(), 'Deadline follows the download completion date');
  assert(expirationButton(selected).closest('.media-card').textContent.includes('Expires in 13 days'), 'Selected card updates its deadline');
  equal(JSON.stringify(media[1]), JSON.stringify(sibling), 'Sibling video remains unchanged');
  equal(expirationButton(sibling).closest('.media-card').textContent, siblingLabel, 'Sibling card remains unchanged');
  equal(JSON.stringify(settings), originalSettings, 'Global retention remains unchanged');
  equal(calls.filter((call) => call.path === '/api/settings/downloads' && call.method === 'PATCH').length, 0, 'Per-video settings never patch global settings');
  assert(calls.filter((call) => call.path === '/api/media' && call.method === 'GET').length > initialLoads, 'Video list refreshes after saving');
  await click(expirationButton(selected));
  equal(videoDays().value, '14', 'Reopening uses the saved video expiration');
});
await test('Any negative per-video expiration keeps only that video indefinitely', async () => {
  const selected = retainedVideo({ title: 'Keep forever' });
  const sibling = retainedVideo({ id: 'sibling', title: 'Still expires' });
  media = [selected, sibling];
  await mount('downloads');
  await click(expirationButton(selected));
  await input(videoDays(), '-5');
  await click(dialogButton('Save expiration'));
  equal(videoRetentionRequests().at(-1).body.retention_days, -5, 'Negative whole number reaches the backend unchanged');
  equal(media[0].retention_days, null, 'Indefinite retention is normalized by the backend');
  equal(media[0].expires_at, null, 'Indefinite video has no expiration deadline');
  assert(expirationButton(selected).closest('.media-card').textContent.includes('Kept indefinitely'), 'Card displays indefinite retention');
  equal(media[1].retention_days, 7, 'Sibling still expires');
  equal(settings.retention_days, 7, 'Global default still expires');
  equal(calls.filter((call) => call.path === '/api/settings/downloads' && call.method === 'PATCH').length, 0, 'Global settings are not changed');
  await click(expirationButton(selected));
  equal(videoDays().value, '-1', 'Indefinite retention reopens as minus one');
});
await test('Queued, downloading, processing, and failed videos can set retention before completion', async () => {
  media = ['queued', 'downloading', 'processing', 'failed'].map((status) => retainedVideo({
    id: status, title: `${status} video`, status, stage: status, progress: 30, retention_days: null, completed_at: null, expires_at: null,
  }));
  const rows = [...media];
  await mount('downloads');
  for (const item of rows) {
    assert(expirationButton(item).closest('.download-row'), `${item.status} settings are on its download row`);
    await click(expirationButton(item));
    equal(videoDays().value, '-1', 'No-expiration input reflects the current video policy');
    await input(videoDays(), '3');
    await click(dialogButton('Save expiration'));
    const updated = media.find((entry) => entry.id === item.id);
    equal(updated.retention_days, 3, `${item.status} video retains its selected days`);
    equal(updated.expires_at, null, `${item.status} video has no deadline before completion`);
    await click(expirationButton(updated));
    equal(videoDays().value, '3', `${item.status} video reopens with its own saved setting`);
    await click(dialogButton('Cancel'));
  }
  equal(videoRetentionRequests().length, 4, 'Each selected row is saved once');
});
await test('Canceling a per-video expiration draft leaves its saved retention unchanged', async () => {
  const selected = retainedVideo();
  media = [selected];
  await mount('downloads');
  await click(expirationButton(selected));
  await input(videoDays(), '21');
  await click(dialogButton('Cancel'));
  equal(document.querySelector('[role="dialog"]'), null, 'Cancel closes the dialog');
  equal(videoRetentionRequests().length, 0, 'Cancel does not save the draft');
  equal(media[0].retention_days, 7, 'Video retains its original expiration');
  await click(expirationButton(selected));
  equal(videoDays().value, '7', 'Canceled draft does not appear on reopening');
});
await test('Zero, fractional, and empty per-video expiration drafts cannot be saved', async () => {
  const selected = retainedVideo();
  media = [selected];
  await mount('downloads');
  await click(expirationButton(selected));
  for (const invalid of ['0', '1.5', '']) {
    await input(videoDays(), invalid);
    await click(dialogButton('Save expiration'));
    assert(document.querySelector('[role="dialog"]'), 'Invalid input keeps the dialog open');
    equal(videoRetentionRequests().length, 0, `Invalid days ${JSON.stringify(invalid)} are never submitted`);
    equal(media[0].retention_days, 7, 'Invalid input cannot alter saved retention');
  }
});
await test('A failed video expiration save keeps the draft open and can be retried', async () => {
  const selected = retainedVideo();
  media = [selected];
  await mount('downloads');
  await click(expirationButton(selected));
  await input(videoDays(), '30');
  retentionSaveFailure = true;
  await click(dialogButton('Save expiration'));
  assert(dialog().textContent.includes('Could not save this video expiration'), 'Video-specific save failure is visible');
  equal(videoDays().value, '30', 'Failed draft remains editable');
  equal(media[0].retention_days, 7, 'Failure leaves persisted expiration unchanged');
  retentionSaveFailure = false;
  await click(dialogButton('Save expiration'));
  equal(document.querySelector('[role="dialog"]'), null, 'Successful retry closes the dialog');
  equal(media[0].retention_days, 30, 'Retry persists the original draft');
  equal(videoRetentionRequests().length, 2, 'The failed request is followed by one explicit retry');
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
  await click(button('Download'));
  assert(find('.recommendation-item').textContent.includes('Converting for browser playback'), 'Recommendation conversion stage shown');
  assert(!find('.recommendation-item').textContent.includes('92%'), 'No misleading percentage');
});

const passed = report.filter((item) => item.passed).length;
document.body.dataset.testStatus = passed === report.length ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${passed}/${report.length} passed`;
