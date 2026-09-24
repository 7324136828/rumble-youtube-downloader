import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import App from '../src/App.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const report = [];
const providers = [{ id: 'youtube', name: 'YouTube', domain: 'youtube.com', enabled: true }, { id: 'vimeo.com', name: 'Vimeo films', domain: 'vimeo.com', enabled: true }];
const video = { id: 'vimeo.com:123', connector: 'vimeo.com', title: 'A film for later', description: 'Coastal birds.', source_url: 'https://vimeo.com/123', verified: false, origins: ['custom_search'], user_added: false };
let root, settings, catalog, pageLinks, calls, failSave, failLoad, failRemove, failTitle, pageSize, fallback, holdList, libraryMedia;
const pollTimers = new Map();
const nativeSetTimeout = window.setTimeout.bind(window);
const nativeClearTimeout = window.clearTimeout.bind(window);
let timerId = 0;
window.setTimeout = (callback, delay, ...args) => {
  if (delay !== 2000) return nativeSetTimeout(callback, delay, ...args);
  const id = --timerId; pollTimers.set(id, () => callback(...args)); return id;
};
window.clearTimeout = (id) => { if (!pollTimers.delete(id)) nativeClearTimeout(id); };
window.fetch = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  const method = options.method || 'GET';
  const body = options.body ? JSON.parse(options.body) : null;
  calls.push({ path: url.pathname, method, body, query: Object.fromEntries(url.searchParams), signal: options.signal });
  let value, error;
  if (url.pathname === '/api/recommendations/settings') value = settings;
  else if (url.pathname === '/api/recommendations/models') value = { models: [{ id: 'test-model', name: 'Test model' }] };
  else if (url.pathname === '/api/recommendations/watch-later') {
    if (method === 'GET') {
      const source = url.searchParams.get('source');
      const rows = catalog.filter((item) => source === 'all' || item.connector === source);
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Math.min(pageSize, Number(url.searchParams.get('limit') || 200));
      value = { items: rows.slice(offset, offset + limit), total: rows.length, revision: settings.revision };
      if (failLoad) error = 'Saved videos are temporarily unavailable';
    } else {
      if (failSave) error = 'Could not save the video list';
      let added = 0, updated = 0;
      const saved = [];
      if (!error) for (const entry of body.videos) {
        const existing = catalog.find((item) => item.source_url === entry.source_url);
        const title = entry.title || existing?.title || entry.source_url;
        const item = { ...existing, ...entry, catalog_id: existing?.catalog_id || catalog.length + 1, connector: entry.source_url.includes('vimeo') ? 'vimeo.com' : 'youtube', title, origins: ['watch_later'], user_added: true, verified: false, title_fetch_status: title === entry.source_url && body.fetch_titles !== false ? 'pending' : 'idle', title_fetch_error: null };
        if (existing) { catalog = catalog.map((old) => old.catalog_id === existing.catalog_id ? item : old); updated++; }
        else { catalog.push(item); added++; }
        saved.push(item);
      }
      if (!error) settings = { ...settings, revision: settings.revision + 1 };
      value = { items: saved, added, updated, revision: settings.revision };
    }
  } else if (url.pathname === '/api/recommendations/watch-later/links') {
    value = { items: pageLinks, total: pageLinks.length };
  } else if (url.pathname === '/api/recommendations/watch-later/preview-links') {
    value = { page_url: body.page_url, total: 3, video_total: 2, items: [
      { url: 'https://vimeo.com/123', video_url: 'https://vimeo.com/123', title: 'Example video', is_video: true, provider: 'vimeo.com' },
      { url: 'https://www.youtube.com/watch?v=abcdefghijk', video_url: 'https://www.youtube.com/watch?v=abcdefghijk', title: 'YouTube video', is_video: true, provider: 'youtube' },
      { url: 'https://example.com/article', video_url: null, title: 'Example article', is_video: false, provider: null },
    ] };
  } else if (url.pathname === '/api/recommendations/watch-later/save-all-links') {
    pageLinks = [
      { id: 1, url: 'https://example.com/video', title: 'Example video', source_page: body.page_url },
      { id: 2, url: 'https://example.com/article', title: 'Example article', source_page: body.page_url },
    ];
    value = { added: 2, updated: 0, total: 2, page_url: body.page_url };
  } else if (/\/watch-later\/links\/\d+$/.test(url.pathname)) {
    pageLinks = pageLinks.filter((item) => item.id !== Number(url.pathname.split('/').at(-1)));
    value = { removed: true };
  } else if (/\/watch-later\/\d+\/title$/.test(url.pathname)) {
    const id = Number(url.pathname.split('/').at(-2));
    if (failTitle) error = 'Title lookup is temporarily unavailable';
    else catalog = catalog.map((item) => item.catalog_id === id ? { ...item, title_fetch_status: 'pending', title_fetch_error: null } : item);
    value = { item: catalog.find((item) => item.catalog_id === id), queued: !error };
  } else if (url.pathname.startsWith('/api/recommendations/watch-later/')) {
    if (failRemove) error = 'Could not remove this saved video';
    else { catalog = catalog.filter((item) => item.catalog_id !== Number(url.pathname.split('/').at(-1))); settings = { ...settings, revision: settings.revision + 1 }; }
    value = { removed: !error, revision: settings.revision };
  } else if (url.pathname === '/api/search') value = { query: url.searchParams.get('q'), source: url.searchParams.get('source'), results: [video], warnings: [] };
  else if (url.pathname === '/api/recommendations') value = { status: 'ready', items: [{ ...video, ...(catalog.some((item) => item.source_url === video.source_url) ? { user_added: true, origins: ['custom_search', 'watch_later'] } : {}) }], warnings: [], fallback_used: fallback };
  else if (url.pathname === '/api/media') {
    if (method === 'POST') {
      libraryMedia = { id: 'watch-later-media', source_url: body.urls[0], title: 'Ready to play', connector: 'generic', status: 'ready', stream_url: '/tests/player.fixture.webm' };
      value = [libraryMedia];
    } else value = libraryMedia ? [libraryMedia] : [];
  } else if (url.pathname === `/api/media/${libraryMedia?.id}`) value = libraryMedia;
  else if (url.pathname === '/api/connectors') value = [];
  else throw new Error(`Unexpected request ${method} ${path}`);
  const response = { ok: !error, status: error ? 502 : 200, json: async () => error ? { detail: error } : value };
  if (holdList && url.pathname === '/api/recommendations/watch-later' && method === 'GET') {
    const held = holdList; holdList = null;
    return new Promise((resolve) => { held.resolve = () => resolve(response); held.signal = options.signal; });
  }
  return response;
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, got ${actual}`);
const find = (selector) => { const item = host.querySelector(selector); assert(item, `Missing ${selector}`); return item; };
const button = (text) => { const item = [...host.querySelectorAll('button')].find((element) => element.textContent.trim() === text); assert(item, `Missing ${text}`); return item; };
const click = (item) => act(async () => item.click());
const navigate = (route) => act(async () => { location.hash = route; window.dispatchEvent(new HashChangeEvent('hashchange')); });
const writes = () => calls.filter((call) => call.path === '/api/recommendations/watch-later' && call.method === 'POST');
const downloads = () => calls.filter((call) => call.path === '/api/media' && call.method === 'POST');
const linkWrites = () => calls.filter((call) => call.path === '/api/recommendations/watch-later/save-all-links');
const linkPreviews = () => calls.filter((call) => call.path === '/api/recommendations/watch-later/preview-links');
const titleWrites = () => calls.filter((call) => /\/watch-later\/\d+\/title$/.test(call.path));
const listReads = () => calls.filter((call) => call.path === '/api/recommendations/watch-later' && call.method === 'GET');
async function poll() {
  const callbacks = [...pollTimers.values()]; pollTimers.clear();
  assert(callbacks.length, 'Pending titles schedule a refresh');
  await act(async () => { callbacks.forEach((callback) => callback()); });
}
async function input(item, value) {
  await act(async () => {
    const prototype = item instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : item instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(item, value);
    item.dispatchEvent(new Event(item instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}
async function upload(value) {
  const transfer = new DataTransfer(); transfer.items.add(new File([typeof value === 'string' ? value : JSON.stringify(value)], 'videos.json', { type: 'application/json' }));
  await act(async () => { find('#watch-later-import').files = transfer.files; find('#watch-later-import').dispatchEvent(new Event('change', { bubbles: true })); });
  await click(button('Import video list'));
  for (let i = 0; i < 20; i++) { await act(async () => new Promise((done) => setTimeout(done, 5))); if (host.querySelector('.success-banner, [role="alert"]')) break; }
}
async function test(name, body, setup = () => {}) {
  settings = { enabled: false, model_id: null, allow_unverified_links: false, allow_ai_title_lookup: false, providers: providers.map((provider) => ({ ...provider })), revision: 0 };
  catalog = []; pageLinks = []; calls = []; failSave = failLoad = failRemove = failTitle = fallback = false; pageSize = 200; holdList = null; libraryMedia = null; pollTimers.clear();
  history.replaceState(null, '', '#watch-later'); setup();
  root = createRoot(host);
  try { await act(async () => root.render(<App />)); await body(); report.push({ name, passed: true }); }
  catch (error) { report.push({ name, passed: false, message: error.message }); }
  finally { await act(async () => root.unmount()); host.replaceChildren(); }
  document.getElementById('results').textContent = report.map((item) => `${item.passed ? 'PASS' : 'FAIL'} ${item.name}${item.message ? ': ' + item.message : ''}`).join('\n');
}

await test('Watch later works with AI off and preserves saved and unverified labels', async () => {
  assert(find('nav [aria-current="page"]').textContent.includes('Watch later'), 'Dedicated sidebar page is selected');
  const item = find('.watch-later-item');
  assert(item.textContent.includes('Saved by you') && item.textContent.includes('Unverified link'), 'User curation is distinct from verification');
  assert(item.textContent.includes('Vimeo films'), 'Configured website name shown');
  equal(calls.filter((call) => call.path === '/api/recommendations').length, 0, 'Catalog does not trigger AI');
  equal(downloads().length, 0, 'Catalog does not download automatically');
}, () => { catalog = [{ ...video, user_added: true, origins: ['watch_later'], catalog_id: 1 }]; });
await test('Each saved item can play an existing download or prepare a missing one', async () => {
  await click(find('[aria-label^="Play saved video:"]'));
  await act(async () => Promise.resolve());
  equal(downloads().length, 1, 'A missing local copy is added to Downloads');
  equal(downloads()[0].body.urls[0], video.source_url, 'Play prepares the selected saved URL');
  equal(location.hash, '#watch/watch-later-media', 'Ready download opens in Watch');
}, () => { catalog = [{ ...video, user_added: true, origins: ['watch_later'], catalog_id: 1 }]; });
await test('Play reuses a ready local copy without downloading it again', async () => {
  await click(find('[aria-label^="Play saved video:"]'));
  equal(downloads().length, 0, 'Existing ready media is reused');
  equal(location.hash, '#watch/already-ready', 'Existing media opens directly in Watch');
}, () => {
  libraryMedia = { id: 'already-ready', source_url: video.source_url, title: video.title, connector: 'generic', status: 'ready', stream_url: '/tests/player.fixture.webm' };
  catalog = [{ ...video, user_added: true, origins: ['watch_later'], catalog_id: 1, media_id: 'already-ready' }];
});
await test('Pasted links save optional metadata, deduplicate batches, and refresh settings', async () => {
  await input(find('#watch-later-urls'), video.source_url);
  await input(find('#watch-later-title'), 'My saved film');
  await input(find('#watch-later-description'), 'A note from me.');
  await click(button('Save for later'));
  equal(writes()[0].body.videos[0].title, 'My saved film', 'Optional title sent');
  equal(writes()[0].body.videos[0].description, 'A note from me.', 'Optional description sent');
  equal(writes()[0].body.fetch_titles, true, 'Title fetching is requested by default');
  equal(writes()[0].body.resolve_redirects, true, 'Redirect resolution is requested before saving');
  equal(find('.watch-later-item h3').textContent, 'My saved film', 'Manual title remains exactly as supplied');
  equal(pollTimers.size, 0, 'Supplied titles do not cause background polling');
  equal(titleWrites().length, 0, 'Supplied title does not need an explicit fetch');
  equal(find('#watch-later-urls').value, '', 'Successful paste clears form');
  await input(find('#watch-later-urls'), 'https://www.youtube.com/watch?v=abcdefghijk\nhttps://vimeo.com/456\nhttps://vimeo.com/456');
  assert(find('#watch-later-title').disabled, 'Per-video title does not apply to a batch');
  await click(button('Save for later'));
  equal(writes()[1].body.videos.length, 2, 'Repeated pasted URL is submitted once');
  assert(calls.filter((call) => call.path === '/api/recommendations/settings').length >= 3, 'Mutations refresh shared recommendation settings');
  equal(downloads().length, 0, 'Saving a batch does not queue downloads');
});
await test('JSON array and videos-object imports preserve metadata and reject malformed files', async () => {
  await upload([{ source_url: video.source_url, title: 'Imported film', description: 'Imported note' }]);
  equal(writes()[0].body.videos[0].description, 'Imported note', 'Array import metadata is preserved');
  await upload({ videos: [{ source_url: 'https://vimeo.com/456', title: 'Second import' }] });
  equal(writes()[1].body.videos[0].title, 'Second import', 'Object import is converted to the API payload');
  const count = writes().length;
  await upload('{ broken json');
  equal(writes().length, count, 'Malformed JSON makes no catalog mutation');
  assert(host.textContent.includes('valid JSON video list'), 'Malformed file error is readable');
  await upload({ videos: [{ title: 'Missing URL' }] });
  equal(writes().length, count, 'Missing URL does not reach backend');
  assert(host.textContent.includes('must have a source_url'), 'Missing required URL is explained');
});
await test('Saved videos display their automatically downloaded thumbnail', async () => {
  const image = find('.watch-later-item-thumbnail img');
  assert(image.getAttribute('src').includes('/api/recommendations/watch-later/1/thumbnail'), 'Saved thumbnail endpoint is rendered');
}, () => { catalog = [{ ...video, catalog_id: 1, thumbnail_url: '/api/recommendations/watch-later/1/thumbnail', thumbnail_fetch_status: 'ready' }]; });
await test('Save all links opens a review window and bulk-adds selected videos', async () => {
  await input(find('#watch-later-page-url'), 'https://example.com/collection');
  await click(button('Save all links'));
  equal(linkPreviews()[0].body.page_url, 'https://example.com/collection', 'Page URL is previewed');
  equal(linkWrites().length, 0, 'Opening the review window does not archive links');
  equal(writes().length, 0, 'Opening the review window does not save videos');
  assert(find('[role="dialog"]'), 'Link review opens as a modal dialog');
  equal(host.querySelectorAll('.link-picker-row').length, 3, 'Every extracted link is visible');
  equal(host.querySelectorAll('.link-picker-row input:not(:disabled)').length, 2, 'Only recognized videos are selectable');
  assert(host.textContent.includes('Not recognized as a configured video'), 'Non-video links are explained');
  await click(host.querySelectorAll('.link-picker-row input:not(:disabled)')[1]);
  await click(button('Add 1 video'));
  equal(writes()[0].body.videos.length, 1, 'Only the selected video is submitted');
  equal(writes()[0].body.videos[0].source_url, 'https://vimeo.com/123', 'Selected canonical URL is saved');
  equal(writes()[0].body.videos[0].title, 'Example video', 'Page title is preserved for thumbnail card metadata');
  equal(host.querySelector('[role="dialog"]'), null, 'Successful bulk save closes the review');
  equal(downloads().length, 0, 'Bulk Watch later saving does not download videos');
});
await test('Text view accepts an externally edited URL list for bulk saving', async () => {
  await input(find('#watch-later-page-url'), 'https://example.com/collection');
  await click(button('Save all links'));
  await click(button('Text view'));
  assert(find('#link-picker-all-text').value.includes('https://example.com/article'), 'Text view exposes every page link for manual copying');
  await input(find('#link-picker-text'), 'https://vimeo.com/456\nhttps://www.youtube.com/watch?v=abcdefghijk\nhttps://vimeo.com/456');
  await click(button('Add 2 videos'));
  equal(writes()[0].body.videos.length, 2, 'Text URLs are trimmed and deduplicated');
  equal(writes()[0].body.videos[0].source_url, 'https://vimeo.com/456', 'Pasted external URL is accepted');
  equal(writes()[0].body.videos[1].title, 'YouTube video', 'Known preview metadata is reused');
});
await test('The review window can still archive every page link separately', async () => {
  await input(find('#watch-later-page-url'), 'https://example.com/collection');
  await click(button('Save all links'));
  await click(button('Archive all page links'));
  equal(linkWrites()[0].body.page_url, 'https://example.com/collection', 'Archive action uses the reviewed page');
  equal(host.querySelectorAll('.watch-later-link-item').length, 2, 'Every extracted page link is shown');
  assert(host.textContent.includes('Example video') && host.textContent.includes('Example article'), 'Video and article links are both retained');
  equal(find('.watch-later-link-item a').getAttribute('href'), 'https://example.com/video', 'Saved links remain directly openable');
  await click(button('Cancel'));
  await click(find('[aria-label="Remove saved link: Example video"]'));
  equal(host.querySelectorAll('.watch-later-link-item').length, 1, 'A saved page link can be removed');
  equal(downloads().length, 0, 'Bulk link saving never downloads linked content');
});
await test('Website filter includes configured disabled sites and removes saved entries', async () => {
  await input(find('[aria-label="Watch later website"]'), 'vimeo.com');
  equal(host.querySelectorAll('.watch-later-item').length, 1, 'Saved list is filtered by custom website');
  equal(calls.filter((call) => call.path === '/api/recommendations/watch-later' && call.method === 'GET').at(-1).query.source, 'vimeo.com', 'Filter sends custom id');
  await click(find('[aria-label="Remove saved video: A film for later"]'));
  equal(host.querySelector('.watch-later-item'), null, 'Removed saved video disappears');
  equal(catalog.length, 1, 'Other websites remain saved');
}, () => { settings.providers[1].enabled = false; catalog = [{ ...video, catalog_id: 1 }, { ...video, catalog_id: 2, connector: 'youtube', source_url: 'https://www.youtube.com/watch?v=abcdefghijk' }]; });
await test('Saved-list pagination appends results without replacing the first page', async () => {
  equal(host.querySelectorAll('.watch-later-item').length, 2, 'First page shown');
  await click(button('Load more saved videos'));
  equal(host.querySelectorAll('.watch-later-item').length, 3, 'Next page is appended');
  equal(calls.filter((call) => call.path === '/api/recommendations/watch-later').at(-1).query.offset, '2', 'Pagination requests next offset');
}, () => { pageSize = 2; catalog = [1, 2, 3].map((catalog_id) => ({ ...video, catalog_id, source_url: `https://vimeo.com/${catalog_id}` })); });
await test('Save and removal failures preserve the form and existing saved videos', async () => {
  failSave = true;
  await input(find('#watch-later-urls'), 'https://vimeo.com/456');
  await click(button('Save for later'));
  equal(find('#watch-later-urls').value, 'https://vimeo.com/456', 'Failed save preserves pasted URLs');
  assert(host.textContent.includes('Could not save the video list'), 'Save error visible');
  failRemove = true;
  await click(find('[aria-label="Remove saved video: A film for later"]'));
  equal(host.querySelectorAll('.watch-later-item').length, 1, 'Failed removal retains the saved item');
}, () => { catalog = [{ ...video, catalog_id: 1 }]; });
await test('Manual search can save for later without downloading and keeps its saved state', async () => {
  await navigate('search?q=birds&source=vimeo.com');
  const searchCount = calls.filter((call) => call.path === '/api/search').length;
  await click(find('[aria-label="Watch later: A film for later"]'));
  equal(writes()[0].body.videos[0].description, video.description, 'Search metadata is preserved in saved entry');
  assert(find('[aria-label="Saved for later: A film for later"]').disabled, 'Saved state survives shared settings refresh');
  equal(downloads().length, 0, 'Watch later action does not download');
  equal(calls.filter((call) => call.path === '/api/search').length, searchCount, 'Saving does not clear or repeat the manual search');
  assert(location.hash.includes('source=vimeo.com'), 'Save stays on search results');
});
await test('Recommendations show fallback origins and save for later without downloading', async () => {
  assert(find('.recommendation-fallback-note').textContent.includes('chosen at random'), 'Fallback explanation is visible');
  assert(find('.recommendation-item').textContent.includes('Website search'), 'Candidate origin is shown');
  await click(find('[aria-label="Watch later: A film for later"]'));
  assert(find('.recommendation-item').textContent.includes('Saved by you'), 'Curated origin appears after refresh');
  assert(find('.recommendation-item').textContent.includes('Unverified link'), 'Saving does not promote verification');
  equal(downloads().length, 0, 'Saving recommendation does not download');
}, () => { settings.enabled = true; settings.model_id = 'test-model'; fallback = true; history.replaceState(null, '', '#feed'); });

await test('Untitled saves display background progress then refresh the title once without changing verification', async () => {
  await input(find('#watch-later-urls'), video.source_url);
  await click(button('Save for later'));
  assert(button('Fetching title…').disabled, 'Pending title button is disabled');
  assert(find('.watch-later-title-status').textContent.includes('Fetching title'), 'Background status is shown');
  const settingsCount = calls.filter((call) => call.path === '/api/recommendations/settings').length;
  const card = find('.watch-later-item');
  catalog = catalog.map((item) => ({ ...item, title: 'Fetched coastal film', title_fetch_status: 'ready' }));
  await poll();
  equal(find('.watch-later-item'), card, 'Polling keeps the existing card mounted');
  equal(find('.watch-later-item h3').textContent, 'Fetched coastal film', 'Fetched title appears without reload');
  assert(card.textContent.includes('Saved by you') && card.textContent.includes('Unverified link'), 'A fetched title does not claim verification');
  equal(calls.filter((call) => call.path === '/api/recommendations/settings').length, settingsCount + 1, 'Title change invalidates recommendations once');
  equal(pollTimers.size, 0, 'Polling stops after completion');
  equal(downloads().length, 0, 'Title lookup does not download');
  equal(calls.filter((call) => call.path === '/api/recommendations').length, 0, 'Title lookup works without AI');
});
await test('AI-fetched titles are visibly labeled and remain unverified', async () => {
  const item = find('.watch-later-item');
  assert(item.textContent.includes('AI-generated title'), 'AI title source is disclosed');
  assert(item.textContent.includes('Unverified link'), 'AI title does not verify the video');
}, () => { catalog = [{ ...video, catalog_id: 1, title: 'AI supplied title', title_fetch_status: 'ready', title_fetch_method: 'ai' }]; });
await test('Failed automatic titles can be entered directly on the saved item', async () => {
  assert(host.textContent.includes('Add a title below'), 'Automatic title failure has an actionable explanation');
  await input(find('#watch-later-manual-title-1'), 'My manual title');
  await click(button('Save title'));
  equal(writes()[0].body.fetch_titles, false, 'Manual title does not start another lookup');
  equal(writes()[0].body.resolve_redirects, false, 'Already resolved saved URL is updated in place');
  equal(find('.watch-later-item h3').textContent, 'My manual title', 'Manual title replaces the URL label');
}, () => { catalog = [{ ...video, catalog_id: 1, title: video.source_url, title_fetch_status: 'unavailable', title_fetch_error: 'A video title could not be found. You can enter one manually.' }]; });
await test('Existing untitled entries can fetch and retry failed title lookups safely', async () => {
  await click(button('Fetch title'));
  equal(titleWrites()[0].path, '/api/recommendations/watch-later/1/title', 'Existing item uses its catalog id');
  equal(titleWrites()[0].query.force, undefined, 'A URL placeholder does not require forced replacement');
  catalog = catalog.map((item) => ({ ...item, title_fetch_status: 'unavailable', title_fetch_error: 'No public title was found. <b>Try later</b>' }));
  await poll();
  assert(find('.watch-later-title-error').textContent.includes('<b>Try later</b>'), 'Failure is rendered as plain text');
  equal(host.querySelector('.watch-later-title-error b'), null, 'Failure markup is not executed');
  equal(pollTimers.size, 0, 'Failed lookup stops automatic polling');
  failTitle = true;
  await click(button('Retry title'));
  assert(find('.watch-later-title-error').textContent.includes('temporarily unavailable'), 'Queue request failure remains inline');
  failTitle = false;
  await click(button('Retry title'));
  catalog = catalog.map((item) => ({ ...item, title: 'Recovered title', title_fetch_status: 'ready', title_fetch_error: null }));
  await poll();
  equal(find('.watch-later-item h3').textContent, 'Recovered title', 'Retry can recover');
  equal(host.querySelector('.watch-later-title-error'), null, 'Successful title clears failure');
}, () => { catalog = [{ ...video, catalog_id: 1, title: video.source_url, title_fetch_status: 'idle' }]; });
await test('Code-shaped imported titles can be replaced with a forced lookup', async () => {
  await click(button('Attempt title fetch'));
  equal(titleWrites()[0].query.force, 'true', 'Suspicious titles request a forced refresh');
  equal(find('.watch-later-item h3').textContent, 'thl("omhmotk56f0",0);', 'Existing label remains visible while lookup runs');
}, () => { catalog = [{ ...video, catalog_id: 1, title: 'thl("omhmotk56f0",0);', user_title: 'thl("omhmotk56f0",0);', title_fetch_status: 'idle' }]; });
await test('Generic Video titles offer a forced title lookup', async () => {
  await click(button('Attempt title fetch'));
  equal(titleWrites()[0].query.force, 'true', 'Generic titles request a forced refresh');
  equal(find('.watch-later-item h3').textContent, 'Video', 'Generic label remains visible while lookup runs');
}, () => { catalog = [{ ...video, catalog_id: 1, title: 'Video', user_title: 'Video', title_fetch_status: 'idle' }]; });
await test('Quality badge titles can be fetched, retried after failure, and replaced after success', async () => {
  equal(titleWrites().length, 0, 'Existing titles are not replaced automatically');
  await click(button('Attempt title fetch'));
  equal(titleWrites()[0].query.force, 'true', '1440pCC can be replaced with a forced lookup');
  equal(find('.watch-later-item h3').textContent, '1440pCC', 'The current title stays visible during lookup');
  assert(button('Fetching title…').disabled, 'A pending title cannot queue another lookup');
  await click(button('Fetching title…'));
  equal(titleWrites().length, 1, 'Clicking the pending action does not queue a duplicate');

  catalog = catalog.map((item) => ({ ...item, title_fetch_status: 'unavailable', title_fetch_error: 'A video title could not be found. You can enter one manually.' }));
  await poll();
  equal(find('.watch-later-item h3').textContent, '1440pCC', 'Failed lookup preserves the previous title');
  assert(find('.watch-later-title-error').textContent.includes('Add a title below'), 'A failure is explained even with an existing title');
  assert(!find('#watch-later-manual-title-1').disabled, 'Manual title entry is available after failure');
  failTitle = true;
  await click(button('Retry title'));
  assert(find('.watch-later-title-error').textContent.includes('temporarily unavailable'), 'A failed retry request is visible');
  equal(find('.watch-later-item h3').textContent, '1440pCC', 'A request failure also preserves the title');

  failTitle = false;
  await click(button('Retry title'));
  assert(titleWrites().every((call) => call.query.force === 'true'), 'Retries also request replacement of the existing title');
  equal(find('.watch-later-item h3').textContent, '1440pCC', 'Retry keeps the current title while pending');
  catalog = catalog.map((item) => ({ ...item, title: 'Coastal birds at sunset', user_title: null, title_fetch_status: 'ready', title_fetch_error: null }));
  await poll();
  equal(find('.watch-later-item h3').textContent, 'Coastal birds at sunset', 'Successful lookup displays the fetched title');
  equal(host.querySelector('.watch-later-title-error'), null, 'Success clears the failure');
  equal(host.querySelector('.watch-later-manual-title'), null, 'Success clears the manual fallback form');
  assert(!button('Attempt title fetch').disabled, 'Title fetching remains available after success');
}, () => { catalog = [{ ...video, catalog_id: 1, title: '1440pCC', user_title: '1440pCC', title_fetch_status: 'idle' }]; });
await test('Any existing title offers an explicit replacement lookup without automatic changes', async () => {
  equal(titleWrites().length, 0, 'A saved title does not trigger a lookup when the page opens');
  equal(pollTimers.size, 0, 'A completed title does not start polling');
  await click(button('Attempt title fetch'));
  equal(titleWrites()[0].query.force, 'true', 'A normal-looking title can also be replaced explicitly');
  equal(find('.watch-later-item h3').textContent, video.title, 'An explicit lookup preserves the title while pending');
  assert(button('Fetching title…').disabled, 'Replacement is disabled while pending');
}, () => { catalog = [{ ...video, catalog_id: 1, user_title: video.title, title_fetch_status: 'ready' }]; });
await test('Title polling preserves pagination and refreshes every loaded page', async () => {
  await click(button('Load more saved videos'));
  equal(host.querySelectorAll('.watch-later-item').length, 201, 'Both pages are visible');
  catalog = catalog.map((item) => item.catalog_id === 201 ? { ...item, title: 'Last page title', title_fetch_status: 'ready' } : item);
  const before = listReads().length;
  await poll();
  equal(host.querySelectorAll('.watch-later-item').length, 201, 'Polling does not collapse to page one');
  equal(listReads().slice(before).map((call) => call.query.offset).join(','), '0,200', 'All loaded pages are refreshed');
  assert(host.textContent.includes('Last page title'), 'Later page receives its fetched title');
  equal(pollTimers.size, 0, 'Completed paginated list stops polling');
}, () => { catalog = Array.from({ length: 201 }, (_, index) => ({ ...video, catalog_id: index + 1, source_url: `https://vimeo.com/${index + 1}`, title: index === 200 ? '' : `Saved film ${index + 1}`, title_fetch_status: index === 200 ? 'pending' : 'idle' })); });
await test('Stale title polls cannot replace a different website filter', async () => {
  const held = {}; holdList = held;
  await poll();
  await input(find('[aria-label="Watch later website"]'), 'youtube');
  assert(held.signal.aborted, 'Switching websites cancels the older title poll');
  await act(async () => held.resolve());
  equal(host.querySelectorAll('.watch-later-item').length, 1, 'Filtered list remains intact');
  equal(find('.watch-later-item h3').textContent, 'YouTube saved title', 'Stale custom-site title is discarded');
  equal(pollTimers.size, 0, 'No pending videos in this filter means no polling');
}, () => { catalog = [{ ...video, catalog_id: 1, title: '', title_fetch_status: 'pending' }, { ...video, catalog_id: 2, connector: 'youtube', title: 'YouTube saved title', source_url: 'https://www.youtube.com/watch?v=abcdefghijk' }]; });
await test('Title polling recovers from list failure and cancels on leaving Watch later', async () => {
  failLoad = true;
  await poll();
  equal(host.querySelectorAll('.watch-later-item').length, 1, 'Temporary poll failure keeps saved items');
  assert(find('.watch-later-poll-error').textContent.includes('Retrying'), 'Automatic retry is explained');
  failLoad = false;
  await poll();
  equal(host.querySelector('.watch-later-poll-error'), null, 'Successful refresh clears poll failure');
  const held = {}; holdList = held;
  await poll();
  await navigate('library');
  assert(held.signal.aborted, 'Leaving page aborts in-flight title refresh');
  await act(async () => held.resolve());
  equal(pollTimers.size, 0, 'Unmount stops scheduled polling');
  equal(host.querySelector('.watch-later-item'), null, 'Late response cannot render old page');
}, () => { catalog = [{ ...video, catalog_id: 1, title: '', title_fetch_status: 'pending' }]; });

document.body.dataset.testStatus = report.every((item) => item.passed) ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${report.filter((item) => item.passed).length}/${report.length} passed`;
