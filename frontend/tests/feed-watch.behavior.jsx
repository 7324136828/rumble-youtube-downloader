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
const fixtures = ['one', 'two', 'three'].map((id, index) => ({ id, title: `Saved video ${index + 1}`, stream_url: '/tests/player.fixture.webm', connector: 'youtube', source_url: `https://www.youtube.com/watch?v=${id}`, duration: 120, uploader: 'Test channel' }));
let videos;
let root;
let apiFails;
let deleteFails;
let navigation;
window.confirm = () => true;
window.matchMedia = () => ({ matches: true });
Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('Denied'); } } });
window.fetch = async (url, options = {}) => {
  const deletion = options.method === 'DELETE';
  const failed = deletion ? deleteFails : apiFails;
  let data = deletion ? {} : videos;
  const path = new URL(url, location.href).pathname;
  const match = path.match(/^\/api\/media\/([^/]+)(.*)$/);
  if (match && !failed && !deletion) {
    const video = videos.find((item) => item.id === match[1]);
    if (video) {
      if (match[2] === '/playback' && options.method === 'PATCH') {
        video.playback_format = JSON.parse(options.body).format;
        video.playback_preference_explicit = true;
      }
      if (match[2] === '/conversions/mp3' && options.method === 'POST') {
        video.conversions = { ...video.conversions, mp3: { format: 'mp3', status: 'converting' } };
        data = video.conversions.mp3;
      } else {
        if (!match[2] && video.conversions?.mp3?.status === 'converting') {
          video.playback_format = 'mp3';
          video.playback_preference_explicit = true;
          video.conversions.mp3 = { format: 'mp3', status: 'completed', stream_url: `/audio-${video.id}.mp3`, download_url: `/download-${video.id}.mp3` };
        }
        data = video;
      }
    }
  }
  const snapshot = structuredClone(data);
  return { ok: !failed, status: failed ? 500 : 200, json: async () => failed ? { detail: deletion ? 'Delete failed on server' : 'Server unavailable' } : snapshot };
};
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const find = (selector) => { const value = host.querySelector(selector); assert(value, `Missing ${selector}`); return value; };
const click = async (element) => act(async () => element.click());
const dispatch = async (element, event) => act(async () => element.dispatchEvent(event));

const render = async (Component, props = {}) => act(async () => root.render(<Component navigate={(route) => { navigation = route; }} {...props} />));
async function test(name, body) {
  videos = structuredClone(fixtures);
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
await test('Feed advances to the next video when the active video ends', async () => {
  await render(FeedPage);
  sessionStorage.setItem('clipfeed.position.one', '119');
  await dispatch(find('.cf-feed-item:not([inert]) video'), new Event('ended'));
  const cards = host.querySelectorAll('.cf-feed-item');
  assert(cards[0].hasAttribute('inert') && !cards[1].hasAttribute('inert'), 'Finished video advances to the next feed item');
  assert(sessionStorage.getItem('clipfeed.position.one') === '0', 'Finished video resets its saved position');
  assert(!cards[1].querySelector('video').paused, 'Next video starts playing');
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
await test('Speed and sound carry across already-mounted Feed items and into Watch', async () => {
  localStorage.setItem('clipfeed.muted', '0');
  await render(FeedPage);
  const select = find('.cf-feed-item:not([inert]) select[aria-label="Playback speed"]');
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, '1.5');
  await dispatch(select, new Event('change', { bubbles: true }));
  assert([...host.querySelectorAll('video')].every((item) => item.playbackRate === 1.5), 'Speed synchronizes with the preloaded player');
  await dispatch(find('.cf-feed-scroll'), new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
  const active = find('.cf-feed-item:not([inert]) video');
  assert(active.playbackRate === 1.5 && !active.muted, 'Next Feed item uses speed and sound');
  await render(WatchPage, { videoId: 'two' });
  assert(find('video').playbackRate === 1.5 && !find('video').muted, 'Watch restores the same settings');
});
await test('Watch prefers selected MP3 over compatible MP4 and can return to video', async () => {
  videos[0] = { ...videos[0], media_kind: 'video', playback_format: 'mp3', playback_preference_explicit: true, thumbnail_url: '/cover.jpg', conversions: {
    mp3: { status: 'completed', stream_url: '/audio-one.mp3', download_url: '/download-one.mp3' },
    mp4: { status: 'completed', stream_url: '/compatible.mp4' },
  } };
  await render(WatchPage, { videoId: 'one' });
  assert(find('video').getAttribute('src') === '/audio-one.mp3', 'Selected MP3 takes priority');
  assert(find('.cvp-audio-artwork img').getAttribute('src') === '/cover.jpg', 'MP3 displays video thumbnail');
  const toggle = [...host.querySelectorAll('.cf-watch-actions button')].find((item) => item.textContent.includes('Play video'));
  assert(toggle, 'Completed MP3 offers return to video');
  await click(toggle);
  assert(videos[0].playback_format === 'original', 'Format switch persisted through API');
  assert(find('video').getAttribute('src') === '/compatible.mp4', 'Compatible video plays');
  assert(!host.querySelector('.cvp-audio-artwork'), 'Video display restored');
});
await test('Automatic playback prefers compatible MP4, then MP3, before the original', async () => {
  videos[0] = { ...videos[0], media_kind: 'video', playback_format: 'original', playback_preference_explicit: false, thumbnail_url: '/cover.jpg', conversions: {
    mp3: { status: 'completed', stream_url: '/audio-one.mp3', download_url: '/download-one.mp3' },
  } };
  videos[1] = { ...videos[1], media_kind: 'video', playback_format: 'original', playback_preference_explicit: false, thumbnail_url: '/cover.jpg', conversions: {
    mp3: { status: 'completed', stream_url: '/audio-two.mp3', download_url: '/download-two.mp3' },
    mp4: { status: 'completed', stream_url: '/compatible-two.mp4', download_url: '/compatible-two.mp4' },
  } };
  videos[2] = { ...videos[1], id: 'three', media_kind: 'audio', title: 'Saved video 3', conversions: {
    mp3: { status: 'completed', stream_url: '/audio-three.mp3', download_url: '/download-three.mp3' },
    mp4: { status: 'completed', stream_url: '/compatible-three.mp4', download_url: '/compatible-three.mp4' },
  } };
  await render(WatchPage, { videoId: 'one' });
  assert(find('video').getAttribute('src') === '/audio-one.mp3', 'Completed MP3 avoids the original source');
  assert(find('.cvp-audio-artwork img').getAttribute('src') === '/cover.jpg', 'MP3 fallback retains artwork');
  await render(WatchPage, { videoId: 'two' });
  assert(find('video').getAttribute('src') === '/compatible-two.mp4', 'Compatible MP4 has highest automatic priority');
  assert(!host.querySelector('.cvp-audio-artwork'), 'MP4 displays as video');
  await render(WatchPage, { videoId: 'three' });
  assert(find('video').getAttribute('src') === '/compatible-three.mp4', 'Rendered audio MP4 remains preferred');
  assert(!host.querySelector('.cvp-audio-artwork'), 'Rendered thumbnail comes from the MP4 video track');
});
await test('An uploaded audio item shows its custom artwork in Feed', async () => {
  videos[1] = { ...videos[1], media_kind: 'audio', playback_format: 'original', connector: 'upload', source_url: 'upload:two', stream_url: '/uploaded.m4a', thumbnail_url: '/custom-cover.jpg' };
  await render(FeedPage, { videoId: 'two' });
  const active = find('.cf-feed-item:not([inert])');
  assert(active.querySelector('video').getAttribute('src') === '/uploaded.m4a', 'Original uploaded audio is playable');
  assert(active.querySelector('.cvp-audio-artwork img').getAttribute('src') === '/custom-cover.jpg', 'Uploaded cover remains displayed');
  assert(!active.querySelector('[aria-label="Copy original video link"]'), 'Uploaded Feed item has no external source action');
  await render(WatchPage, { videoId: 'two' });
  assert(![...host.querySelectorAll('.cf-watch-actions button')].some((item) => item.textContent.includes('Copy link')), 'Uploaded Watch item has no copy source action');
  assert(!host.querySelector('.cf-description-heading a'), 'Uploaded Watch item has no pseudoURL original link');
});
await test('Watch conversion polls to completion and changes to MP3 playback', async () => {
  await render(WatchPage, { videoId: 'one' });
  await click(find('.cf-watch-actions [aria-label="Convert to MP3"]'));
  assert(find('.cf-watch-actions .is-working').disabled, 'Conversion remains visible while running');
  await act(async () => new Promise((resolve) => setTimeout(resolve, 1300)));
  assert(find('video').getAttribute('src') === '/audio-one.mp3', 'Completed conversion becomes active playback');
  assert(host.querySelector('.cvp-audio-artwork'), 'Audio artwork appears after conversion');
  assert(videos[0].playback_format === 'mp3', 'Server-selected format retained');
});
await test('Feed retains conversion progress when navigating away and back', async () => {
  await render(FeedPage);
  await click(find('.cf-feed-conversions [aria-label="Convert to MP3"]'));
  const scroll = find('.cf-feed-scroll');
  await dispatch(scroll, new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
  await dispatch(scroll, new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
  assert(find('.cf-feed-conversions .is-working').disabled, 'Returning to the item retains conversion progress');
  await act(async () => new Promise((resolve) => setTimeout(resolve, 1300)));
  assert(find('.cf-feed-item:not([inert]) video').getAttribute('src') === '/audio-one.mp3', 'Resumed polling selects completed MP3');
});
const passed = results.every((result) => result.passed);
document.body.dataset.testStatus = passed ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${results.filter((result) => result.passed).length}/${results.length} passed`;





