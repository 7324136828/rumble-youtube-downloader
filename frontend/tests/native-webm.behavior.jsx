import React from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import CustomVideoPlayer from '../src/components/CustomVideoPlayer.jsx';

// Intentionally no mocked HTMLMediaElement APIs: these checks decode real local
// VP9/Opus and AV1/Opus WebM streams in the installed Chromium browser.
const host = document.getElementById('test-root');
const report = document.getElementById('results');
const results = [];
const assert = (condition, message) => { if (!condition) throw new Error(message); };

function eventWhen(video, event, predicate = () => false) {
  if (predicate()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      video.removeEventListener(event, success);
      video.removeEventListener('error', failure);
    };
    const success = () => { cleanup(); resolve(); };
    const failure = () => { cleanup(); reject(new Error(`Media error ${video.error?.code}: ${video.error?.message}`)); };
    const timer = setTimeout(() => { cleanup(); reject(new Error(`Timed out waiting for ${event}`)); }, 3000);
    video.addEventListener(event, success, { once: true });
    video.addEventListener('error', failure, { once: true });
  });
}

for (const [codec, contentType] of [['vp9', 'video/webm; codecs="vp9,opus"'], ['av1', 'video/webm; codecs="av01.0.00M.08,opus"']]) {
  const root = createRoot(host);
  const ref = React.createRef();
  const name = `${codec.toUpperCase()}/Opus WebM decodes, plays, and seeks in CustomVideoPlayer`;
  try {
    flushSync(() => root.render(<CustomVideoPlayer ref={ref} src={`./native-webm.${codec}.webm`} title={`${codec} native fixture`} muted />));
    const video = ref.current;
    assert(video instanceof HTMLVideoElement, 'Player must expose the actual video element');
    assert(video.canPlayType(contentType) !== '', `Browser must support ${contentType}`);
    await eventWhen(video, 'loadeddata', () => video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA);
    assert(video.videoWidth === 96 && video.videoHeight === 64, 'Decoded frame dimensions must match fixture');
    assert(Number.isFinite(video.duration) && video.duration >= 2 && video.duration < 2.1, 'Actual fixture duration must load');
    await video.play();
    assert(!video.paused, 'Native play promise must resolve into playback');
    video.pause();
    const seeked = eventWhen(video, 'seeked');
    video.currentTime = 1;
    await seeked;
    assert(Math.abs(video.currentTime - 1) < 0.1, 'Native seek must reach one second');
    assert(video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA, 'Seek target must decode a frame');
    assert(video.error === null, 'Native decoder must report no error');
    assert(!host.querySelector('[role="alert"]'), 'Custom controls must not display a format error');
    results.push(`PASS ${name}`);
  } catch (error) {
    results.push(`FAIL ${name}: ${error.message}`);
  } finally {
    flushSync(() => root.unmount());
    host.replaceChildren();
    report.textContent = results.join('\n');
  }
}

const passed = results.filter((result) => result.startsWith('PASS')).length;
document.body.dataset.testStatus = passed === results.length ? 'passed' : 'failed';
document.body.dataset.testCount = String(results.length);
report.textContent += `\n${passed}/${results.length} tests passed\nBrowser: ${navigator.userAgent}`;
await fetch('/__native-webm-result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ passed: passed === results.length, report: report.textContent }) });
