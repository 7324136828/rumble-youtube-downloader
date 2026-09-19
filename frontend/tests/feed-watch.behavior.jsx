import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import FeedPage from '../src/components/FeedPage.jsx';
import WatchPage from '../src/components/WatchPage.jsx';

// Browser integration checks use deterministic API/media responses, with no external videos.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const results = [];
const state = new WeakMap();
const media = (video) => {
  if (!state.has(video)) state.set(video, { paused: true, currentTime: 0, duration: 120, volume: 1, muted: false, playbackRate: 1 });
  return state.get(video);
};
for (const name of ['paused', 'currentTime', 'duration', 'volume', 'muted', 'playbackRate']) {
  Object.defineProperty(HTMLMediaElement.prototype, name, { configurable: true, get() { return media(this)[name]; }, set(value) { media(this)[name] = value; } });
}
Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value() { media(this).paused = false; this.dispatchEvent(new Event('play')); return Promise.resolve(); } });
Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value() { if (!this.paused) { media(this).paused = true; this.dispatchEvent(new Event('pause')); } } });
const videos = ['one', 'two', 'three'].map((id, index) => ({ id, title: `Saved video ${index + 1}`, stream_url: '/tests/player.fixture.webm', connector: 'youtube', source_url: `https://www.youtube.com/watch?v=${id}`, duration: 120, uploader: 'Test channel' }));
let root;
let apiFails;
let deleteFails;
let navigation;
window.confirm = () => true;
window.matchMedia = () => ({ matches: true });
Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('Denied'); } } });
window.fetch = async (_url, options = {}) => {
  const deletion = options.method === 'DELETE';
  const failed = deletion ? deleteFails : apiFails;
  return { ok: !failed, status: failed ? 500 : 200, json: async () => failed ? { detail: deletion ? 'Delete failed on server' : 'Server unavailable' } : deletion ? {} : videos };
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const find = (selector) => { const value = host.querySelector(selector); assert(value, `Missing ${selector}`); return value; };
const click = async (element) => act(async () => element.click());
const dispatch = async (element, event) => act(async () => element.dispatchEvent(event));

const render = async (Component, props = {}) => act(async () => root.render(<Component navigate={(route) => { navigation = route; }} {...props} />));
async function test(name, body) {
  apiFails = deleteFails = false; navigation = ''; sessionStorage.clear(); localStorage.clear();
  root = createRoot(host);
  try { await body(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
  finally { await act(async () => root.unmount()); host.replaceChildren(); }
  document.getElementById('results').textContent = results.map((result) => `${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? `: ${result.error}` : ''}`).join('\n');
}

await test('Feed distinguishes API failure from an empty library and retries', async () => {
  apiFails = true;
  await render(FeedPage);
  assert(find('[role="alert"]').textContent.includes('Server unavailable'), 'Server failure must be visible');
  assert(!host.textContent.includes('Start with a video you love'), 'Failure must not appear empty');
  apiFails = false;
  await click(find('[role="alert"] button'));
  assert(host.querySelectorAll('.cf-feed-item').length === 3, 'Retry loads real items');
});
await test('Feed arrow navigation pauses the previous item and keeps custom controls', async () => {
  await render(FeedPage);

  const scroll = find('.cf-feed-scroll');
  await dispatch(scroll, new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));

  const cards = host.querySelectorAll('.cf-feed-item');
  assert(cards[0].hasAttribute('inert') && !cards[1].hasAttribute('inert'), 'Only second card is active, inert values=' + [...cards].map((card) => card.hasAttribute('inert')).join(',') + '; scroll=' + scroll.scrollTop);
  assert(cards[0].querySelector('video').paused, 'Previous video pauses');
  assert(!cards[1].querySelector('video').paused, 'Active video plays');
  assert([...host.querySelectorAll('video')].every((video) => !video.controls), 'Native controls stay disabled');
  assert([...host.querySelectorAll('video')].filter((video) => !video.paused).length === 1, 'Only one video is playing');
  await dispatch(scroll, new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
  assert(!cards[0].hasAttribute('inert') && cards[1].hasAttribute('inert'), 'ArrowUp returns to the first card');
  assert([...host.querySelectorAll('video')].filter((video) => !video.paused).length === 1, 'Only one video plays after navigating back');
});
await test('Feed wheel navigation attaches after asynchronous API loading', async () => {
  await render(FeedPage);
  await dispatch(find('.cf-feed-scroll'), new WheelEvent('wheel', { deltaY: 90, bubbles: true, cancelable: true }));
  assert(!host.querySelectorAll('.cf-feed-item')[1].hasAttribute('inert'), 'Wheel activates next video');
});
await test('Feed opens the same video in Watch and preserves playback position', async () => {
  await render(FeedPage, { videoId: 'two' });
  const active = find('.cf-feed-item:not([inert])');
  const element = active.querySelector('video');
  element.currentTime = 34;
  await click(active.querySelector('[aria-label="Open in Watch view"]'));
  assert(navigation === 'watch/two', 'Mode switch keeps video identity');
  assert(sessionStorage.getItem('clipfeed.position.two') === '34', 'Mode switch saves exact position');
});
await test('Feed preserves its item when the server rejects deletion', async () => {
  deleteFails = true;
  await render(FeedPage);
  await click(find('.cf-feed-item:not([inert]) [aria-label="Delete video"]'));
  assert(host.querySelectorAll('.cf-feed-item').length === 3, 'Failed deletion must not remove local data');
  assert(find('.cf-view-toast').textContent.includes('Could not delete'), 'Failed deletion shows feedback');
});
await test('Clipboard rejection does not show a false copied message', async () => {
  await render(FeedPage);
  await click(find('.cf-feed-item:not([inert]) [aria-label="Copy original video link"]'));
  assert(find('.cf-view-toast').textContent.includes('Could not copy'), 'Clipboard failure is truthful');
});
await test('Watch restores position, then switches back to the same feed video', async () => {
  sessionStorage.setItem('clipfeed.position.two', '42');
  await render(WatchPage, { videoId: 'two' });
  const element = find('video');
  await dispatch(element, new Event('loadedmetadata'));
  assert(element.currentTime === 42, 'Position restores after metadata');
  await click(find('.cf-watch-feed-button'));
  assert(navigation === 'feed/two', 'Return to Feed keeps identity');
});
await test('Watch autoplay follows the next saved video and respects the toggle', async () => {
  await render(WatchPage, { videoId: 'one' });
  await dispatch(find('video'), new Event('ended'));
  assert(navigation === 'watch/two', 'Autoplay opens next item');
  navigation = '';
  await click(find('.cf-autoplay-toggle input'));
  await dispatch(find('video'), new Event('ended'));
  assert(navigation === '', 'Disabled autoplay stays on current item');
});
await test('Watch preserves the current video after a server deletion failure', async () => {
  deleteFails = true;
  await render(WatchPage, { videoId: 'one' });
  await click(find('[aria-label="Delete this video"]'));
  assert(find('.cf-watch-title').textContent === 'Saved video 1', 'Current video remains');
  assert(!navigation, 'Failed delete must not navigate');
  assert(find('.cf-view-toast').textContent.includes('Could not delete'), 'Failure is visible');
});
const passed = results.every((result) => result.passed);
document.body.dataset.testStatus = passed ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${results.filter((result) => result.passed).length}/${results.length} passed`;





