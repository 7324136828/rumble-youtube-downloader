import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import CustomVideoPlayer from '../src/components/CustomVideoPlayer.jsx';
import PlaybackSettings from '../src/components/PlaybackSettings.jsx';

// Run in a real browser; media APIs are deterministic fakes for race/error coverage.
// No testing framework or external video/network dependency is needed.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const results = [];
const originals = [];
let root;
let props;
let lastVideo;
let playCalls;
let pauseCalls;
let playBehavior;
let fakeFullscreen;
let fakePip;
let fullscreenRequests;
let pipRequests;
let pipExits;
let webkitFullscreenRequests;
let webkitPresentationChanges;
const state = new WeakMap();
const mediaState = (video) => {
  if (!state.has(video)) state.set(video, { paused: true, currentTime: 0, duration: NaN, volume: 1, muted: false, playbackRate: 1, error: null, webkitPresentationMode: 'inline', webkitDisplayingFullscreen: false });
  return state.get(video);
};
function fakeProperty(object, name, descriptor) {
  originals.push([object, name, Object.getOwnPropertyDescriptor(object, name)]);
  Object.defineProperty(object, name, { configurable: true, ...descriptor });
}
for (const name of ['paused', 'currentTime', 'duration', 'volume', 'muted', 'playbackRate', 'error']) {
  fakeProperty(HTMLMediaElement.prototype, name, {
    get() { return mediaState(this)[name]; },
    set(value) {
      mediaState(this)[name] = value;
      if (name === 'volume' || name === 'muted') this.dispatchEvent(new Event('volumechange'));
      if (name === 'playbackRate') this.dispatchEvent(new Event('ratechange'));
    },
  });
}
fakeProperty(HTMLMediaElement.prototype, 'load', { value() {} });
fakeProperty(HTMLMediaElement.prototype, 'play', { value() { playCalls++; return playBehavior(this); } });
fakeProperty(HTMLMediaElement.prototype, 'pause', { value() {
  pauseCalls++;
  if (!this.paused) { mediaState(this).paused = true; this.dispatchEvent(new Event('pause')); }
} });
fakeProperty(document, 'fullscreenEnabled', { value: true });
fakeProperty(document, 'pictureInPictureEnabled', { value: true });
fakeProperty(document, 'fullscreenElement', { get: () => fakeFullscreen });
fakeProperty(document, 'pictureInPictureElement', { get: () => fakePip });
fakeProperty(HTMLElement.prototype, 'requestFullscreen', { value: async function () {
  fullscreenRequests++;
  fakeFullscreen = this;
  document.dispatchEvent(new Event('fullscreenchange'));
} });
fakeProperty(document, 'exitFullscreen', { value: async () => {
  fakeFullscreen = null;
  document.dispatchEvent(new Event('fullscreenchange'));
} });
fakeProperty(HTMLVideoElement.prototype, 'requestPictureInPicture', { value: async function () {
  pipRequests++;
  fakePip = this;
  this.dispatchEvent(new Event('enterpictureinpicture'));
} });
fakeProperty(document, 'exitPictureInPicture', { value: async () => {
  pipExits++;
  const video = fakePip;
  fakePip = null;
  video?.dispatchEvent(new Event('leavepictureinpicture'));
} });
fakeProperty(HTMLVideoElement.prototype, 'webkitPresentationMode', {
  get() { return mediaState(this).webkitPresentationMode; },
});
fakeProperty(HTMLVideoElement.prototype, 'webkitDisplayingFullscreen', {
  get() { return mediaState(this).webkitDisplayingFullscreen; },
});
fakeProperty(HTMLVideoElement.prototype, 'webkitSupportsPresentationMode', { value(mode) { return mode === 'picture-in-picture'; } });
fakeProperty(HTMLVideoElement.prototype, 'webkitSetPresentationMode', { value(mode) {
  webkitPresentationChanges++;
  mediaState(this).webkitPresentationMode = mode;
  this.dispatchEvent(new Event('webkitpresentationmodechanged'));
} });
fakeProperty(HTMLVideoElement.prototype, 'webkitEnterFullscreen', { value() {
  webkitFullscreenRequests++;
  mediaState(this).webkitDisplayingFullscreen = true;
  this.dispatchEvent(new Event('webkitbeginfullscreen'));
} });
fakeProperty(HTMLVideoElement.prototype, 'webkitExitFullscreen', { value() {
  mediaState(this).webkitDisplayingFullscreen = false;
  this.dispatchEvent(new Event('webkitendfullscreen'));
} });

const assert = (condition, message) => { if (!condition) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, received ${actual}`);
const find = (selector) => { const element = host.querySelector(selector); assert(element, `Missing ${selector}`); return element; };
const button = (label) => find(`button[aria-label="${label}"]`);
const event = async (element, name, data) => act(async () => { element.dispatchEvent(name === 'keydown' ? new KeyboardEvent(name, { key: data, bubbles: true, cancelable: true }) : new Event(name, { bubbles: true })); });
const click = async (element) => act(async () => element.click());
const successfulPlay = (video) => {
  mediaState(video).paused = false;
  video.dispatchEvent(new Event('play'));
  video.dispatchEvent(new Event('playing'));
  return Promise.resolve();
};
async function render(next = {}) {
  props = { ...props, ...next };
  await act(async () => root.render(<CustomVideoPlayer {...props} />));
  lastVideo = host.querySelector('video');
  return lastVideo;
}
async function metadata(duration = 120, position = 0) {
  mediaState(lastVideo).duration = duration;
  mediaState(lastVideo).currentTime = position;
  await event(lastVideo, 'loadedmetadata');
  await event(lastVideo, 'timeupdate');
}
async function input(element, value) {
  const prototype = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, String(value));
  await event(element, element instanceof HTMLSelectElement ? 'change' : 'input');
}
async function test(name, body) {
  localStorage.clear();
  localStorage.setItem('clipfeed.muted', '0');
  playCalls = pauseCalls = fullscreenRequests = pipRequests = pipExits = webkitFullscreenRequests = webkitPresentationChanges = 0;
  fakeFullscreen = fakePip = null;
  playBehavior = successfulPlay;
  props = { src: undefined, title: 'Test video' };
  root = createRoot(host);
  try { await body(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
  finally { await act(async () => root.unmount()); host.replaceChildren(); }
  document.getElementById('results').textContent = results.map((result) => `${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? `: ${result.error}` : ''}`).join('\n');
}

await test('Custom controls expose accessible names and the raw video ref', async () => {
  const ref = React.createRef();
  await render({ ref });
  equal(lastVideo.controls, false, 'Native controls disabled');
  equal(ref.current, lastVideo, 'Forwarded ref');
  equal(find('[role="region"]').getAttribute('aria-label'), 'Test video video player', 'Player label');
  equal(find('input[aria-label="Seek video"]').disabled, true, 'Unknown duration disables seeking');
  assert(button('Play (K)') && button('Mute (M)') && find('select[aria-label="Playback speed"]'), 'Labeled controls');
});

await test('Metadata, seek range, timestamps, and callback values stay in sync', async () => {
  let reported;
  await render({ onTimeUpdate: (time, duration) => { reported = [time, duration]; } });
  await metadata(125, 10);
  const range = find('input[aria-label="Seek video"]');
  equal(range.max, '125', 'Duration sets maximum');
  equal(range.value, '10', 'Playback sets range');
  equal(range.getAttribute('aria-valuetext'), '0:10 of 2:05', 'Accessible timestamp');
  await input(range, 35.5);
  equal(lastVideo.currentTime, 35.5, 'Seeking changes media position');
  equal(reported.join(','), '35.5,125', 'Callback receives seconds and duration');
  await metadata(Infinity, 0);
  equal(range.disabled, true, 'Non-finite duration is not seekable');
});

await test('Volume, mute, and speed update the video and visible controls', async () => {
  await render();
  await input(find('[aria-label="Volume"]'), 0.35);
  equal(lastVideo.volume, 0.35, 'Volume');
  await click(button('Mute (M)'));
  equal(lastVideo.muted, true, 'Mute');
  equal(find('[aria-label="Volume"]').value, '0', 'Muted volume UI');
  await click(button('Unmute (M)'));
  equal(lastVideo.muted, false, 'Unmute');
  await input(find('select'), 1.5);
  equal(lastVideo.playbackRate, 1.5, 'Playback speed');
});

await test('Blocked sound autoplay keeps the preference and prompts for a user gesture', async () => {
  const changed = [];
  playBehavior = (video) => video.muted ? successfulPlay(video) : Promise.reject(new DOMException('Sound blocked', 'NotAllowedError'));
  await render({ src: '/mock-video.mp4', autoPlay: true, onMutedChange: (value) => changed.push(value) });
  equal(playCalls, 1, 'No silent muted retry');
  equal(lastVideo.muted, false, 'Sound preference remains enabled');
  equal(lastVideo.paused, true, 'Waits for user gesture');
  equal(changed.length, 0, 'No forced mute notification');
  equal(localStorage.getItem('clipfeed.muted'), '0', 'Saved preference remains enabled');
  assert(find('[role="status"]').textContent.includes('Press play to start with sound'), 'Clear start prompt');
  playBehavior = successfulPlay;
  await click(button('Play (K)'));
  equal(lastVideo.paused, false, 'User can start playback');
  equal(lastVideo.muted, false, 'User gesture starts with sound');
});

await test('Playback settings and mute control persist across different players', async () => {
  localStorage.clear();
  await act(async () => root.render(<PlaybackSettings />));
  const setting = find('#playback-start-sound');
  equal(setting.checked, false, 'Sound starts disabled');
  await click(setting);
  equal(localStorage.getItem('clipfeed.muted'), '0', 'Setting saves sound');
  await render({ src: '/first.mp4', autoPlay: true });
  equal(lastVideo.muted, false, 'Next player starts with sound');
  await click(button('Mute (M)'));
  await render({ key: 'another-player', src: '/second.mp4' });
  equal(lastVideo.muted, true, 'Next player preserves muted preference');
});

await test('Speed survives a source change, browser metadata reset, and player remount', async () => {
  await render({ src: '/first.mp4' });
  await input(find('select'), 1.5);
  equal(localStorage.getItem('clipfeed.speed'), '1.5', 'Speed saved');
  await render({ src: '/second.mp4' });
  mediaState(lastVideo).playbackRate = 1;
  await metadata();
  equal(lastVideo.playbackRate, 1.5, 'Metadata reapplies saved speed');
  await render({ key: 'another-player', src: '/third.mp4' });
  equal(lastVideo.playbackRate, 1.5, 'New player restores saved speed');
  equal(find('select').value, '1.5', 'Speed control reflects preference');
});

await test('Audio keeps thumbnail visible while playing and preserves the media ref', async () => {
  const ref = React.createRef();
  await render({ ref, src: '/audio.mp3', audioOnly: true, poster: '/cover.jpg', autoPlay: true });
  equal(lastVideo.paused, false, 'Audio plays');
  equal(ref.current, lastVideo, 'Audio preserves ref contract for watch history');
  equal(find('.cvp-audio-artwork img').getAttribute('src'), '/cover.jpg', 'Artwork remains during playback');
  assert(find('[role="region"]').getAttribute('aria-label').includes('audio player'), 'Audio is labeled');
  equal(host.querySelector('[aria-label="Picture-in-picture"]'), null, 'Audio does not offer a blank PiP window');
  await render({ src: '/video.mp4', audioOnly: false });
  equal(host.querySelector('.cvp-audio-artwork'), null, 'Switching back to video removes artwork overlay');
});

await test('Inactive players never start and active transitions pause playback', async () => {
  await render({ src: '/mock-video.mp4', autoPlay: true, active: false });
  equal(playCalls, 0, 'Inactive initial autoplay');
  await render({ active: true });
  equal(lastVideo.paused, false, 'Activation plays');
  await render({ active: false });
  equal(lastVideo.paused, true, 'Deactivation pauses');
  equal(find('[role="region"]').tabIndex, -1, 'Inactive region removed from tab order');
});

await test('Late autoplay rejection cannot restart an inactive item', async () => {
  let rejectPlay;
  playBehavior = () => new Promise((resolve, reject) => { rejectPlay = reject; });
  await render({ src: '/mock-video.mp4', autoPlay: true });
  await render({ active: false });
  await act(async () => rejectPlay(new DOMException('Late rejection', 'NotAllowedError')));
  equal(playCalls, 1, 'Stale rejection does not retry');
  equal(lastVideo.muted, false, 'Stale rejection does not change mute');
  equal(host.querySelector('[role="status"]'), null, 'Stale rejection does not show an error');
});

await test('Late successful playback is stopped after the item becomes inactive', async () => {
  let resolvePlay;
  playBehavior = (video) => new Promise((resolve) => { resolvePlay = () => { successfulPlay(video); resolve(); }; });
  await render({ src: '/mock-video.mp4', autoPlay: true });
  await render({ active: false });
  await act(async () => resolvePlay());
  equal(lastVideo.paused, true, 'Late successful play is paused');
  assert(button('Play (K)'), 'Controls remain paused');
});

await test('Abort errors do not trigger muted retries or misleading notices', async () => {
  playBehavior = () => Promise.reject(new DOMException('Interrupted', 'AbortError'));
  await render({ src: '/mock-video.mp4', autoPlay: true });
  equal(playCalls, 1, 'No muted retry for an interrupted request');
  equal(host.querySelector('[role="status"]'), null, 'No notice for expected cancellation');
});

await test('Controlled mute changes notify the owner and follow new props', async () => {
  const changes = [];
  await render({ muted: true, onMutedChange: (value) => changes.push(value) });
  equal(lastVideo.muted, true, 'Initial controlled mute');
  await click(button('Unmute (M)'));
  equal(changes.join(','), 'false', 'Owner receives unmute request');
  await render({ muted: false });
  equal(lastVideo.muted, false, 'New mute prop reaches media');
  assert(button('Mute (M)'), 'Control follows new mute prop');
});

await test('Editable overlay shortcuts remain available for text editing', async () => {
  await render({ children: <div contentEditable="plaintext-only" suppressContentEditableWarning>Editable</div> });
  await event(find('[contenteditable]'), 'keydown', 'k');
  equal(playCalls, 0, 'Editable K is not intercepted');
});
await test('Scoped shortcuts clamp seeking and do not intercept form controls', async () => {
  await render();
  await metadata(20, 18);
  const region = find('[role="region"]');
  await event(region, 'keydown', 'ArrowRight');
  equal(lastVideo.currentTime, 20, 'Seek clamps at duration');
  await event(region, 'keydown', 'j');
  equal(lastVideo.currentTime, 10, 'J seeks backward');
  await event(find('[aria-label="Volume"]'), 'keydown', 'k');
  equal(playCalls, 0, 'Slider keyboard remains native');
  await event(region, 'keydown', 'k');
  equal(lastVideo.paused, false, 'K plays');
  await event(region, 'keydown', ' ');
  equal(lastVideo.paused, true, 'Space pauses');
});

await test('Feed arrow keys navigate once or bubble to page navigation', async () => {
  let next = 0;
  await render({ variant: 'feed', onNext: () => next++ });
  await event(find('[role="region"]'), 'keydown', 'ArrowDown');
  equal(next, 1, 'Callback invoked once');
  await render({ onNext: undefined });
  const arrow = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
  find('[role="region"]').dispatchEvent(arrow);
  equal(arrow.defaultPrevented, false, 'Page can handle arrow');
  equal(lastVideo.volume, 1, 'Feed navigation does not change volume');
});

await test('Fullscreen requests the custom player wrapper and follows exit events', async () => {
  await render();
  await click(button('Fullscreen (F)'));
  equal(fullscreenRequests, 1, 'Fullscreen request');
  equal(fakeFullscreen, find('[role="region"]'), 'Custom controls remain inside fullscreen');
  await click(button('Exit fullscreen (F)'));
  equal(fakeFullscreen, null, 'Fullscreen exited');
  assert(button('Fullscreen (F)'), 'Control resets after fullscreen exit');
});

await test('Picture-in-picture enters and exits through the standard browser API', async () => {
  await render();
  await click(button('Picture-in-picture'));
  equal(pipRequests, 1, 'Picture-in-picture requested');
  equal(fakePip, lastVideo, 'Current video entered picture-in-picture');
  await click(button('Exit picture-in-picture'));
  equal(pipExits, 1, 'Picture-in-picture exited');
  equal(fakePip, null, 'Picture-in-picture element cleared');
});

await test('iPhone WebKit fallbacks support fullscreen and picture-in-picture', async () => {
  const fullscreenDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenEnabled');
  const pipDescriptor = Object.getOwnPropertyDescriptor(document, 'pictureInPictureEnabled');
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: false });
  Object.defineProperty(document, 'pictureInPictureEnabled', { configurable: true, value: false });
  try {
    await render();
    await click(button('Fullscreen (F)'));
    equal(webkitFullscreenRequests, 1, 'WebKit fullscreen requested');
    await click(button('Exit fullscreen (F)'));
    equal(lastVideo.webkitDisplayingFullscreen, false, 'WebKit fullscreen exited');
    await click(button('Picture-in-picture'));
    equal(webkitPresentationChanges, 1, 'WebKit picture-in-picture requested');
    equal(lastVideo.webkitPresentationMode, 'picture-in-picture', 'WebKit presentation mode entered');
    await click(button('Exit picture-in-picture'));
    equal(lastVideo.webkitPresentationMode, 'inline', 'WebKit presentation mode exited');
  } finally {
    Object.defineProperty(document, 'fullscreenEnabled', fullscreenDescriptor);
    Object.defineProperty(document, 'pictureInPictureEnabled', pipDescriptor);
  }
});

await test('Media errors provide accessible retry and recover when retried', async () => {
  let conversions = 0;
  await render({ formatErrorActions: <button type="button" onClick={() => conversions++}>Convert to MP4</button> });
  mediaState(lastVideo).error = { code: 4 };
  await event(lastVideo, 'error');
  assert(find('[role="alert"]').textContent.includes('format'), 'Format error explanation');
  await click(find('.cvp-error button:not(.cvp-retry)'));
  equal(conversions, 1, 'Format recovery action is available inside the error');
  await click(find('.cvp-retry'));
  equal(host.querySelector('[role="alert"]'), null, 'Retry clears error');
  equal(lastVideo.paused, false, 'Retry requests playback');
});

for (const [object, name, descriptor] of originals.reverse()) {
  if (descriptor) Object.defineProperty(object, name, descriptor);
  else delete object[name];
}
const passed = results.filter((result) => result.passed).length;
document.body.dataset.testStatus = passed === results.length ? 'passed' : 'failed';
document.body.dataset.testCount = String(results.length);
document.getElementById('results').textContent += `\n${passed}/${results.length} tests passed`;

