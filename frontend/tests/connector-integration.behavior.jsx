import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import ConnectorIntegrationSettings from '../src/components/ConnectorIntegrationSettings.jsx';
import RecommendationPanel from '../src/components/RecommendationPanel.jsx';
import KeywordLibraryPage from '../src/components/KeywordLibraryPage.jsx';
import { RecommendationProvider, useRecommendations } from '../src/components/RecommendationContext.jsx';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const report = [];
const suggestion = { id: 'youtube:abcdefghijk', title: 'River wildlife', source_url: 'https://www.youtube.com/watch?v=abcdefghijk', connector: 'youtube', uploader: 'Nature channel', thumbnail_url: null };
let root, calls, status, settings, connectFailure, recommendationResponse, keywordItems;
window.fetch = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  const body = options.body ? JSON.parse(options.body) : undefined;
  const method = options.method || 'GET';
  calls.push({ path: url.pathname, method, body, query: Object.fromEntries(url.searchParams) });
  let value, error;
  if (url.pathname === '/api/connector/status') value = { ...status };
  else if (url.pathname === '/api/connector/settings') {
    status = { ...status, ...body };
    value = { ...status };
  } else if (url.pathname === '/api/connector/connect') {
    if (connectFailure) error = 'The Connector is not reachable.';
    else status = { ...status, session_id: 'activity-session', registered_tools: ['search_videos', 'download_videos'], last_error: null };
    value = { ...status };
  } else if (url.pathname === '/api/recommendations/settings') {
    if (method === 'PATCH') settings = { ...settings, ...body, revision: settings.revision + 1 };
    value = { ...settings };
  } else if (url.pathname === '/api/recommendations') value = await recommendationResponse(body);
  else if (url.pathname === '/api/connector/impressions') value = { recorded: status.enabled };
  else if (url.pathname === '/api/video-keywords') {
    const query = url.searchParams.get('q') || '';
    const matchingRequests = calls.filter((call) => call.path === '/api/video-keywords' && call.query.q === query);
    value = { query, keywords: keywordItems, videos: [], status: { pending: query && matchingRequests.length === 1 ? 1 : 0 } };
  }
  else throw new Error(`Unexpected test request ${method} ${path}`);
  return { ok: !error, status: error ? 502 : 200, json: async () => error ? { detail: error } : value };
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, got ${actual}`);
const find = (selector) => { const item = host.querySelector(selector); assert(item, `Missing ${selector}`); return item; };
const button = (label) => { const item = [...host.querySelectorAll('button')].find((el) => el.textContent.trim() === label); assert(item, `Missing button ${label}`); return item; };
const click = (item) => act(async () => { item.click(); await new Promise((done) => setTimeout(done, 0)); });
const posts = (path) => calls.filter((call) => call.path === path && call.method === 'POST');
const impressions = () => posts('/api/connector/impressions');
async function input(item, value) {
  await act(async () => {
    const proto = item instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(item, value);
    item.dispatchEvent(new Event(item instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}
async function mount(component = <ConnectorIntegrationSettings />) {
  root = createRoot(host);
  await act(async () => root.render(component));
}
function PanelHarness() {
  const { updateSettings } = useRecommendations();
  return <><button onClick={() => updateSettings({ enabled: false })}>Turn off recommendations</button><RecommendationPanel context="history" /></>;
}
const mountPanel = () => mount(<RecommendationProvider><PanelHarness /></RecommendationProvider>);
async function test(name, body) {
  root = null; calls = []; connectFailure = false;
  status = { enabled: false, base_url: 'http://127.0.0.1:8000', connector_url: 'http://127.0.0.1:8301', session_id: null, registered_tools: [], pending_events: 0, last_error: null };
  settings = { enabled: true, model_id: 'test-model', revision: 0, providers: [{ id: 'youtube', name: 'YouTube', enabled: true }, { id: 'rumble', name: 'Rumble', enabled: true }] };
  recommendationResponse = async () => ({ status: 'ready', items: [suggestion], keywords: [] });
  keywordItems = [{ keyword: 'wildlife', count: 2 }, { keyword: 'rivers', count: 1 }];
  try { await body(); report.push({ name, passed: true }); }
  catch (error) { report.push({ name, passed: false, message: error.message }); }
  finally {
    if (root) await act(async () => root.unmount());
    host.replaceChildren();
    delete document.visibilityState;
  }
  document.getElementById('results').textContent = report.map((item) => `${item.passed ? 'PASS' : 'FAIL'} ${item.name}${item.message ? ': ' + item.message : ''}`).join('\n');
}

await test('Connector setup is lazy and explicitly enables tools and logging', async () => {
  await mount();
  equal(calls.length, 0, 'Closed settings do not fetch');
  await click(find('.connector-integration > summary'));
  equal(calls[0].path, '/api/connector/status', 'Opening reads status');
  assert(!find('input[role="switch"]').checked, 'Integration starts disabled');
  assert(host.textContent.includes('independently of the AI recommendations switch'), 'Independent switch explained');
  await click(find('input[role="switch"]'));
  assert(status.enabled, 'Enabling persists preference');
  await click(button('Connect now'));
  assert(host.textContent.includes('activity-session'), 'Created system session is shown');
  assert(host.textContent.includes('2 tools registered'), 'Registration result shown');
  await click(find('input[role="switch"]'));
  assert(!status.enabled, 'User can stop tools and logging');
});

await test('An unavailable Connector displays the error and supports retry', async () => {
  status.enabled = true;
  connectFailure = true;
  await mount();
  await click(find('.connector-integration > summary'));
  await click(button('Connect now'));
  assert(find('[role="alert"]').textContent.includes('not reachable'), 'Failure shown');
  connectFailure = false;
  await click(button('Retry connection'));
  equal(host.querySelector('[role="alert"]'), null, 'Retry clears error');
  assert(host.textContent.includes('activity-session'), 'Retry connects');
});

await test('Advanced downloader address can be saved without enabling the integration', async () => {
  await mount();
  await click(find('.connector-integration > summary'));
  await click(find('.connector-integration-advanced > summary'));
  await input(find('#connector-downloader-url'), 'http://localhost:8100');
  await click(button('Save address'));
  equal(status.base_url, 'http://localhost:8100', 'Reachable address saved');
  assert(!status.enabled, 'Address changes preserve off switch');
});

await test('Displayed recommendations are logged once with their context and metadata', async () => {
  await mountPanel();
  equal(find('.recommendation-item h3').textContent, suggestion.title, 'Suggestion is rendered');
  equal(impressions().length, 1, 'One impression batch');
  equal(impressions()[0].body.context, 'history', 'History context is preserved');
  equal(impressions()[0].body.items[0].source_url, suggestion.source_url, 'Shown video URL is logged');
  assert(/^[0-9a-f-]{36}$/.test(impressions()[0].body.event_id), 'Unique event identifier');
  await act(async () => document.dispatchEvent(new Event('visibilitychange')));
  equal(impressions().length, 1, 'Visibility events do not duplicate the same result');
  await click(find('button[aria-label="Refresh recommendations"]'));
  equal(impressions().length, 2, 'Newly displayed response logs a new impression');
  assert(impressions()[0].body.event_id !== impressions()[1].body.event_id, 'Refresh has a separate identifier');
});

await test('Hidden-page recommendations are logged only after they become visible', async () => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
  await mountPanel();
  equal(impressions().length, 0, 'Hidden results are not impressions');
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  await act(async () => document.dispatchEvent(new Event('visibilitychange')));
  equal(impressions().length, 1, 'Visible results are logged');
});

await test('Large video metadata stays within impression field and byte limits', async () => {
  recommendationResponse = async () => ({ status: 'ready', items: Array.from({ length: 22 }, (_, index) => ({
    ...suggestion,
    id: '界'.repeat(2000),
    source_url: `https://www.youtube.com/watch?v=video${index}`,
    title: '界'.repeat(1000),
    uploader: '界'.repeat(1000),
    description: '界'.repeat(10000),
    thumbnail_url: `https://images.example.com/${'界'.repeat(1900)}`,
  })) });
  await mountPanel();
  const payload = impressions()[0].body;
  equal(payload.items.length, 20, 'Impression count is bounded');
  assert(new TextEncoder().encode(JSON.stringify(payload)).length < 256000, 'Unicode metadata fits the durable payload limit');
  for (const item of payload.items) {
    assert((item.title?.length || 0) <= 500, 'Title length is bounded');
    assert((item.uploader?.length || 0) <= 500, 'Uploader length is bounded');
    assert((item.description?.length || 0) <= 2000, 'Description length is bounded');
    assert(item.source_url.startsWith('https://www.youtube.com/watch?v='), 'Source URL is preserved');
  }
});

await test('Stale responses cannot log impressions after the source changes', async () => {
  let finishOld;
  recommendationResponse = (body) => body.source === 'all'
    ? new Promise((resolve) => { finishOld = resolve; })
    : Promise.resolve({ status: 'ready', items: [{ ...suggestion, title: 'Current source result' }] });
  await mountPanel();
  await input(find('select[aria-label="Recommendation websites"]'), 'youtube');
  equal(impressions().length, 1, 'New source impression recorded');
  await act(async () => finishOld({ status: 'ready', items: [{ ...suggestion, title: 'Stale result' }] }));
  equal(impressions().length, 1, 'Stale response was not recorded');
  equal(impressions()[0].body.items[0].title, 'Current source result', 'Only current result sent');
});

await test('Turning recommendations off prevents pending responses from being logged', async () => {
  let finish;
  recommendationResponse = () => new Promise((resolve) => { finish = resolve; });
  await mountPanel();
  await click(button('Turn off recommendations'));
  await act(async () => finish({ status: 'ready', items: [suggestion] }));
  equal(impressions().length, 0, 'Disabled panel emits no impressions');
  equal(host.querySelector('.recommendation-panel'), null, 'Disabled recommendations are hidden');
});

await test('Video topics searches share a session across background refreshes', async () => {
  await mount(<KeywordLibraryPage navigate={() => {}} />);
  const topicCalls = () => calls.filter((call) => call.path === '/api/video-keywords');
  equal(topicCalls()[0].query.session, undefined, 'Opening all topics is not a search event');
  await click(find('.keyword-cloud button'));
  const firstSearch = topicCalls().at(-1);
  equal(firstSearch.query.q, 'wildlife', 'Cloud selection is a keyword search');
  assert(firstSearch.query.session && firstSearch.query.session.length <= 64, 'Keyword search has a bounded session ID');
  await act(async () => new Promise((done) => setTimeout(done, 2100)));
  assert(topicCalls().length >= 3, 'Pending keyword enrichment triggers a refresh');
  equal(topicCalls().at(-1).query.session, firstSearch.query.session, 'Refresh reuses the same event identity');
  await input(find('input[aria-label="Search videos by keyword"]'), 'rivers');
  await click(button('Search'));
  assert(topicCalls().at(-1).query.session !== firstSearch.query.session, 'A different query starts a fresh search session');
  await click(find('button[aria-label="Clear keyword search"]'));
  equal(topicCalls().at(-1).query.session, undefined, 'Clearing topics does not log a blank search');
});

await test('Video topics initially show 30 words and reveal 30 more at a time', async () => {
  keywordItems = Array.from({ length: 65 }, (_, index) => ({ keyword: `topic ${index + 1}`, count: 65 - index }));
  await mount(<KeywordLibraryPage navigate={() => {}} />);
  equal(host.querySelectorAll('.keyword-cloud button').length, 30, 'Initial cloud is limited to the top 30 words');
  equal(button('Add more (30)').getAttribute('aria-controls'), 'keyword-cloud', 'Expansion identifies the word cloud');
  await click(button('Add more (30)'));
  equal(host.querySelectorAll('.keyword-cloud button').length, 60, 'First expansion reveals 30 more words');
  await click(button('Add more (5)'));
  equal(host.querySelectorAll('.keyword-cloud button').length, 65, 'Final expansion reveals the remaining words');
  equal([...host.querySelectorAll('button')].some((item) => item.textContent.startsWith('Add more')), false, 'Expansion control is removed when all words are visible');
});

const passed = report.filter((item) => item.passed).length;
document.body.dataset.testStatus = passed === report.length ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${passed}/${report.length} passed`;
