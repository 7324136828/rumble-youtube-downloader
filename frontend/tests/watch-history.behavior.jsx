import React, { act, useCallback, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import useWatchHistory from '../src/hooks/useWatchHistory';
import RecommendationPanel from '../src/components/RecommendationPanel';

// Deterministic native-media events and wall time; no external media or AI calls.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const results = [];
const media = new WeakMap();
const timers = new Map();
let now = 1000;
let visible = 'visible';
let timerId = 0;
let root;
let calls = [];
let failHistory = false;
const originalNow = Object.getOwnPropertyDescriptor(performance, 'now');
const originalVisibility = Object.getOwnPropertyDescriptor(document, 'visibilityState');
const originalSetInterval = window.setInterval;
const originalClearInterval = window.clearInterval;
Object.defineProperty(performance, 'now', { configurable: true, value: () => now });
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visible });
window.setInterval = (callback, interval) => { const id = ++timerId; timers.set(id, { callback, interval }); return id; };
window.clearInterval = (id) => timers.delete(id);
window.fetch = async (path, options = {}) => {
  const call = { path, ...options, body: options.body ? JSON.parse(options.body) : null };
  calls.push(call);
  if (path !== '/api/watch-history') throw new Error(`Unexpected request: ${path}`);
  return { ok: !failHistory, status: failHistory ? 503 : 200, json: async () => failHistory ? { detail: 'History unavailable' } : call.body };
};

function bindMedia(element) {
  if (!element || media.has(element)) return;
  const state = { paused: true, currentTime: 0, ended: false, seeking: false, readyState: 4 };
  media.set(element, state);
  for (const name of Object.keys(state)) {
    Object.defineProperty(element, name, { configurable: true, get: () => state[name], set: (value) => { state[name] = value; } });
  }
}
function Harness({ activeId = 'one', enabled = true }) {
  const players = useRef(new Map());
  const getPlayer = useCallback((id) => players.current.get(id), []);
  useWatchHistory(activeId, getPlayer, enabled);
  return <div>{['one', 'two'].map((id) => <video key={id} data-id={id} preload="none" ref={(element) => {
    if (element) { bindMedia(element); players.current.set(id, element); }
    else players.current.delete(id);
  }} />)}<RecommendationPanel context="watch" videoId={activeId} /></div>;
}
const assert = (value, message) => { if (!value) throw new Error(message); };
const equal = (actual, expected, message) => assert(actual === expected, `${message}: expected ${expected}, received ${actual}`);
const player = (id = 'one') => host.querySelector(`video[data-id="${id}"]`);
const history = () => calls.filter((call) => call.path === '/api/watch-history');
const watched = () => history().reduce((sum, call) => sum + call.body.watched_seconds, 0);
const render = (props = {}) => act(async () => root.render(<Harness {...props} />));
const event = (element, name) => act(async () => element.dispatchEvent(new Event(name)));
const start = async (element) => { element.paused = false; element.ended = false; element.readyState = 4; await event(element, 'playing'); };
const advance = (seconds, element = player()) => { now += seconds * 1000; element.currentTime += seconds; };
const pause = async (element = player()) => { element.paused = true; await event(element, 'pause'); };
const tick = () => act(async () => { for (const { callback } of timers.values()) callback(); });

async function test(name, body) {
  now = 1000; visible = 'visible'; calls = []; failHistory = false;
  root = createRoot(host);
  try { await body(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); }
  finally { await act(async () => root.unmount()); host.replaceChildren(); timers.clear(); }
  document.getElementById('results').textContent = results.map((result) => `${result.passed ? 'PASS' : 'FAIL'} ${result.name}${result.error ? `: ${result.error}` : ''}`).join('\n');
}

await test('Paused and preloaded players create no history or standalone recommendation requests', async () => {
  await render();
  await event(player(), 'loadedmetadata');
  await start(player('two'));
  advance(12, player('two'));
  await tick();
  await pause(player('two'));
  await pause();
  equal(calls.length, 0, 'Inactive playback and metadata do not issue requests');
  equal(host.querySelector('.recommendation-panel'), null, 'Recommendations stay disabled without a provider');
});
await test('Foreground playback batches wall time and reports bounded finite deltas', async () => {
  await render();
  equal([...timers.values()][0].interval, 12000, 'History batching interval');
  await start(player());
  advance(12);
  await tick();
  advance(12);
  await tick();
  await pause();
  equal(history().length, 2, 'Pause does not duplicate the most recent batch');
  equal(watched(), 24, 'Actual playback time accumulated');
  equal(history()[1].body.position_seconds, 24, 'Latest playback position saved');
  for (const call of history()) {
    equal(call.method, 'POST', 'History uses POST');
    equal(call.body.video_id, 'one', 'Only active video recorded');
    assert(Number.isFinite(call.body.watched_seconds) && call.body.watched_seconds >= 0 && call.body.watched_seconds <= 30, 'Watch delta is bounded and finite');
    assert(Number.isFinite(call.body.position_seconds), 'Position is finite');
    equal(call.body.completed, false, 'Ordinary playback does not imply completion');
  }
});
await test('Seeking and buffering do not inflate watched seconds', async () => {
  await render();
  await start(player());
  advance(5);
  player().seeking = true; player().currentTime = 90;
  await event(player(), 'seeking');
  now += 20000;
  player().seeking = false;
  await event(player(), 'seeked');
  advance(3);
  player().readyState = 2;
  await event(player(), 'waiting');
  now += 15000;
  await tick();
  equal(watched(), 8, 'Seek distance and waiting time excluded');
  equal(history()[0].body.position_seconds, 93, 'Seek position preserved separately');
  await start(player());
  advance(2);
  await pause();
  equal(watched(), 10, 'Playback resumes counting after buffering');
});
await test('Switching active videos flushes the old item and ignores stale playback events', async () => {
  await render();
  const previous = player();
  await start(previous);
  advance(4, previous);
  await render({ activeId: 'two' });
  equal(history().length, 1, 'Switch flushes previous video');
  equal(history()[0].body.video_id, 'one', 'Flushed item keeps its identity');
  equal(history()[0].body.watched_seconds, 4, 'Previous foreground seconds recorded');
  assert(history()[0].keepalive, 'Switch flush survives navigation');
  await start(previous);
  advance(20, previous);
  await pause(previous);
  equal(history().length, 1, 'Stale inactive events ignored');
  await start(player('two'));
  advance(3, player('two'));
  await pause(player('two'));
  equal(history()[1].body.video_id, 'two', 'New active item tracked');
  equal(history()[1].body.watched_seconds, 3, 'New item has an independent clock');
});
await test('Hidden tabs and pagehide stop counting and flush with keepalive', async () => {
  await render();
  await start(player());
  advance(2);
  visible = 'hidden';
  await event(document, 'visibilitychange');
  equal(watched(), 2, 'Foreground segment saved before hiding');
  advance(20);
  await tick();
  equal(watched(), 2, 'Hidden playback excluded');
  equal(history().length, 1, 'Hidden position changes do not create history entries');
  visible = 'visible';
  await event(document, 'visibilitychange');
  advance(3);
  await event(window, 'pagehide');
  equal(watched(), 5, 'Visible resumed segment saved at pagehide');
  assert(history().at(-1).keepalive, 'Pagehide uses keepalive');
  advance(10);
  await tick();
  equal(watched(), 5, 'Leaving page does not continue counting');
});
await test('An ended event records completion, and unmount flushes unfinished playback', async () => {
  await render();
  await start(player());
  advance(6);
  player().ended = true; player().paused = true;
  await event(player(), 'ended');
  equal(history()[0].body.completed, true, 'Actual ended event marks completion');
  equal(history()[0].body.watched_seconds, 6, 'End flush includes final seconds');
  await render({ activeId: 'two' });
  await start(player('two'));
  advance(2, player('two'));
  await act(async () => root.render(null));
  equal(history().at(-1).body.video_id, 'two', 'Unmount saves the correct active video');
  equal(history().at(-1).body.watched_seconds, 2, 'Unmount saves unfinished playback');
  equal(history().at(-1).body.completed, false, 'Unmount does not imply completion');
  assert(history().at(-1).keepalive, 'Unmount uses keepalive');
});
await test('Invalid positions and failed saves never emit nonfinite values or retry a delta', async () => {
  await render();
  await start(player());
  advance(2);
  player().currentTime = Infinity;
  await tick();
  equal(history().length, 0, 'Nonfinite positions are not sent');
  player().currentTime = 2;
  failHistory = true;
  await pause();
  equal(history().length, 1, 'Save attempted once with valid data');
  equal(history()[0].body.watched_seconds, 2, 'Pending watch time retained until position is valid');
  await tick();
  equal(history().length, 1, 'Possibly committed delta is not retried');
});

if (originalNow) Object.defineProperty(performance, 'now', originalNow); else delete performance.now;
if (originalVisibility) Object.defineProperty(document, 'visibilityState', originalVisibility); else delete document.visibilityState;
window.setInterval = originalSetInterval;
window.clearInterval = originalClearInterval;
document.body.dataset.testStatus = results.every((result) => result.passed) ? 'passed' : 'failed';
document.getElementById('results').textContent += `\n${results.filter((result) => result.passed).length}/${results.length} passed`;
