import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import LibraryPage from '../src/components/LibraryPage';
import WatchPage from '../src/components/WatchPage';
import TranscriptActions from '../src/components/TranscriptActions';
import '../src/App.css';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const results = [];
let root, media, calls, postFailures, readFailure, readSteps, delayedRead;
const makeVideo = (id) => ({ id, title: `Video ${id}`, connector: 'youtube', source_url: `https://example.com/${id}`, status: 'ready', stream_url: '/tests/player.fixture.webm', download_url: '/tests/player.fixture.webm', created_at: '2026-10-11', description: 'The original video description.', conversions: {} });
const completed = (id, source = 'subtitles') => ({ status: 'completed', source, language: 'en', error_message: null, download_url: `/api/media/${id}/transcript/download`, text_url: `/api/media/${id}/transcript` });
const transcriptText = 'First paragraph, preserved exactly.\n\nSecond paragraph with <img id="transcript-injection" src=x> as plain text.\nFinal words of the whole transcript.';
window.matchMedia = () => ({ matches: true });
Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: () => Promise.resolve() });
Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value: () => {} });
window.fetch = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  const method = options.method || 'GET';
  calls.push({ path: url.pathname, method });
  let value, error;
  const match = url.pathname.match(/^\/api\/media\/([^/]+)\/transcript$/);
  if (match) {
    const id = decodeURIComponent(match[1]);
    const video = media.find((item) => item.id === id);
    if (method === 'POST') {
      if (postFailures.has(id)) error = 'Transcript request rejected';
      else {
        video.transcript = { status: video.connector === 'upload' ? 'transcribing' : 'queued', source: video.connector === 'upload' ? 'whisper' : null };
        value = video.transcript;
      }
    } else if (readFailure) error = 'Transcript temporarily unavailable';
    else if (delayedRead) value = await delayedRead(id);
    else {
      const steps = readSteps[id];
      if (steps?.length) video.transcript = steps.shift();
      value = { ...(video.transcript || { status: 'idle' }), ...(video.transcript?.status === 'completed' ? { text: transcriptText } : {}) };
    }
  } else if (url.pathname === '/api/media') value = media;
  else if (url.pathname === '/api/settings/downloads') value = { retention_days: 7 };
  else if (url.pathname === '/api/watch-history') value = {};
  else throw new Error(`Unexpected request: ${method} ${url.pathname}`);
  return { ok: !error, status: error ? 503 : method === 'POST' ? 202 : 200, json: async () => structuredClone(error ? { detail: error } : value) };
};
const assert = (value, message) => { if (!value) throw new Error(message); };
const find = (selector) => { const node = host.querySelector(selector); assert(node, `Missing ${selector}`); return node; };
const button = (text, parent = host) => { const node = [...parent.querySelectorAll('button')].find((item) => item.textContent.trim() === text); assert(node, `Missing button: ${text}`); return node; };
const click = async (node) => act(async () => node.click());
const wait = async (milliseconds = 1100) => act(async () => new Promise((resolve) => setTimeout(resolve, milliseconds)));
const transcriptCalls = (method) => calls.filter((call) => call.path.endsWith('/transcript') && (!method || call.method === method));
const render = async (element) => act(async () => root.render(element));
async function test(name, action) {
  media = ['one', 'two', 'three'].map(makeVideo);
  calls = []; postFailures = new Set(); readFailure = false; readSteps = {}; delayedRead = null;
  sessionStorage.clear(); localStorage.clear();
  root = createRoot(host);
  try { await action(); results.push({ name, passed: true }); }
  catch (error) { results.push({ name, passed: false, error: error.message }); console.error(error); }
  finally { await act(async () => root.unmount()); host.replaceChildren(); }
  document.getElementById('results').textContent = results.map((item) => `${item.passed ? 'PASS' : 'FAIL'} ${item.name}${item.error ? `: ${item.error}` : ''}`).join('\n');
}

await test('Downloads gets source transcripts, shows progress, downloads TXT, and stops polling', async () => {
  await render(<LibraryPage screen="downloads" navigate={() => {}} />);
  assert(transcriptCalls().length === 0, 'Idle cards do not request transcripts');
  const card = find('[aria-label="Select Video one"]').closest('.media-card');
  readSteps.one = [{ status: 'fetching' }, completed('one')];
  await click(button('Get transcript', card));
  assert(transcriptCalls('POST').length === 1 && transcriptCalls('POST')[0].path.includes('/one/'), 'The chosen media is queued');
  assert(card.textContent.includes('Transcript queued'), 'Queued status shown');
  await wait();
  assert(card.textContent.includes('Checking source captions'), 'Source lookup status shown');
  await wait();
  assert(card.textContent.includes('Source captions'), 'Caption source shown after completion');
  const link = card.querySelector('[aria-label="Download transcript as TXT"]');
  assert(link?.getAttribute('href') === '/api/media/one/transcript/download' && link.hasAttribute('download'), 'Completed transcript uses TXT download endpoint');
  const readCount = transcriptCalls('GET').length;
  await wait();
  assert(transcriptCalls('GET').length === readCount, 'Completed transcript polling stops');
});

await test('Batch transcript requests only selected videos and keeps failed selections for retry', async () => {
  await render(<LibraryPage screen="downloads" navigate={() => {}} />);
  await click(find('[aria-label="Select Video one"]'));
  await click(find('[aria-label="Select Video three"]'));
  postFailures.add('three');
  await click(button('Get transcripts for selected videos'));
  assert(transcriptCalls('POST').length === 2 && transcriptCalls('POST').every((item) => !item.path.includes('/two/')), 'Only selected videos requested');
  assert(!find('[aria-label="Select Video one"]').checked && find('[aria-label="Select Video three"]').checked, 'Only failed selection remains');
  assert(host.textContent.includes('Transcript request rejected'), 'Batch failure visible');
  postFailures.clear();
  await click(button('Get transcripts for selected videos'));
  assert(transcriptCalls('POST').length === 3 && transcriptCalls('POST')[2].path.includes('/three/'), 'Retry only queues failed selection');
  assert(!find('[aria-label="Select Video three"]').checked, 'Successful retry clears selection');
});

await test('Watch description displays the whole transcript as escaped text with paragraphs and toggle', async () => {
  media[0].transcript = completed('one');
  await render(<WatchPage videoId="one" navigate={() => {}} />);
  assert(transcriptCalls().length === 0, 'Completed transcript stays lazy until shown');
  assert(!host.querySelector('.media-transcript-panel'), 'Panel starts hidden');
  await click(button('Show transcript'));
  const text = find('.cf-watch-description .media-transcript-text');
  assert(text.textContent === transcriptText, 'Every paragraph and final word appears');
  assert(getComputedStyle(text).whiteSpace === 'pre-wrap', 'Paragraph whitespace is preserved');
  assert(!host.querySelector('#transcript-injection'), 'Transcript markup is escaped by React');
  assert(find('.cf-watch-description').textContent.includes('The original video description.'), 'Original description remains visible');
  await click(button('Hide transcript'));
  assert(!host.querySelector('.media-transcript-panel'), 'Hide closes transcript');
  await click(button('Show transcript'));
  assert(transcriptCalls('GET').length === 1, 'Completed text is reused when reopened');
});

await test('An uploaded video can generate with Whisper and reveal text automatically when ready', async () => {
  media[0].connector = 'upload';
  media[0].source_url = 'upload:one';
  await render(<WatchPage videoId="one" navigate={() => {}} />);
  await click(button('Show transcript'));
  assert(host.textContent.includes('No transcript yet') && host.textContent.includes('generate one with Whisper'), 'Uploaded media offers Whisper');
  readSteps.one = [completed('one', 'whisper')];
  await click(button('Get transcript'));
  assert(host.textContent.includes('Generating with Whisper'), 'Transcribing status shown');
  await wait();
  assert(find('.media-transcript-text').textContent === transcriptText, 'Open panel updates on completion');
  assert(host.textContent.includes('Generated with Whisper'), 'Whisper source shown');
  assert(find('video').getAttribute('src') === '/tests/player.fixture.webm', 'Transcript does not change playback');
});

await test('A completed transcript read failure is visible and can be retried', async () => {
  media[0].transcript = completed('one');
  readFailure = true;
  await render(<TranscriptActions video={media[0]} showText />);
  await click(button('Show transcript'));
  assert(find('[role="alert"]').textContent.includes('Transcript temporarily unavailable'), 'Read failure visible');
  readFailure = false;
  await click(button('Retry loading transcript'));
  assert(find('.media-transcript-text').textContent === transcriptText, 'Retry loads completed text');
  assert(!host.querySelector('[role="alert"]'), 'Successful read clears error');
});

await test('Failed transcript jobs show a retry action and POST failures stay actionable', async () => {
  media[0].transcript = { status: 'failed', error_message: 'Whisper model unavailable' };
  postFailures.add('one');
  await render(<TranscriptActions video={media[0]} />);
  assert(find('[role="alert"]').textContent.includes('Whisper model unavailable'), 'Job error visible');
  await click(button('Retry transcript'));
  assert(host.textContent.includes('Transcript request rejected'), 'POST error visible');
  assert(button('Retry transcript'), 'Failed submission can still be retried');
  postFailures.clear();
  await click(button('Retry transcript'));
  assert(host.textContent.includes('Transcript queued'), 'Retry queues transcript');
});

await test('A delayed transcript response cannot reveal the previous video after navigation', async () => {
  media[0].transcript = completed('one');
  media[1].transcript = completed('two');
  let resolveFirst;
  delayedRead = (id) => id === 'one' ? new Promise((resolve) => { resolveFirst = resolve; }) : { ...completed('two'), text: 'Second video transcript' };
  await render(<TranscriptActions video={media[0]} showText />);
  await click(button('Show transcript'));
  await render(<TranscriptActions video={media[1]} showText />);
  await click(button('Show transcript'));
  await act(async () => resolveFirst({ ...completed('one'), text: 'Previous video must stay hidden' }));
  assert(find('.media-transcript-text').textContent === 'Second video transcript', 'Response remains associated with selected video');
  assert(!host.textContent.includes('Previous video must stay hidden'), 'Previous text cannot leak');
});

const passed = results.filter((item) => item.passed).length;
document.getElementById('results').textContent += `\n${passed}/${results.length} passed`;
document.body.dataset.testStatus = passed === results.length ? 'passed' : 'failed';
