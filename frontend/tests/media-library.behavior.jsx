import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import LibraryPage from '../src/components/LibraryPage';
import '../src/App.css';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = document.getElementById('test-root');
const report = [];
let root, media, calls, failures, thumbnailFailure;
const makeVideo = (id) => ({ id, title: `Video ${id}`, connector: 'youtube', source_url: `https://example.com/${id}`, status: 'ready', stream_url: '/tests/player.fixture.webm', download_url: '/tests/player.fixture.webm', created_at: '2026-09-24', conversions: {} });
window.fetch = async (path, options = {}) => {
  const url = new URL(path, location.origin);
  const method = options.method || 'GET';
  calls.push({ path: url.pathname, method, body: options.body });
  let value;
  let error;
  if (url.pathname === '/api/media/upload') {
    const file = options.body.get('file');
    if (failures.has(file.name)) error = 'Upload rejected';
    else {
      const audio = file.type.startsWith('audio');
      value = { ...makeVideo(`upload-${media.length}`), title: options.body.get('title') || file.name, connector: 'upload', media_kind: audio ? 'audio' : 'video', thumbnail_url: options.body.get('thumbnail') ? '/uploaded-cover.jpg' : null, conversions: audio ? { mp3: { format: 'mp3', status: 'completed', stream_url: '/uploaded.mp3', download_url: '/uploaded.mp3' } } : {} };
      media = [value, ...media];
    }
  } else if (url.pathname === '/api/media') value = media;
  else if (url.pathname === '/api/settings/downloads') value = { retention_days: 7 };
  else if (/\/conversions\/(mp3|mp4)$/.test(url.pathname)) {
    const id = url.pathname.split('/')[3];
    const format = url.pathname.split('/').at(-1);
    if (failures.has(id)) error = 'Conversion rejected';
    else {
      value = { format, status: 'queued' };
      media = media.map((item) => item.id === id ? { ...item, conversions: { ...item.conversions, [format]: value } } : item);
    }
  } else if (url.pathname.endsWith('/thumbnail')) {
    if (thumbnailFailure) error = 'Thumbnail rejected';
    else {
      const id = url.pathname.split('/')[3];
      value = { ...media.find((item) => item.id === id), thumbnail_url: `/api/media/${id}/thumbnail?v=2` };
      media = media.map((item) => item.id === id ? value : item);
    }
  } else if (url.pathname.startsWith('/api/media/')) value = media.find((item) => item.id === url.pathname.split('/')[3]);
  else throw new Error(`Unexpected request: ${url.pathname}`);
  return { ok: !error, status: error ? 400 : 200, json: async () => error ? { detail: error } : value };
};
const assert = (value, message) => { if (!value) throw new Error(message); };
const find = (selector) => { const found = host.querySelector(selector); assert(found, `Missing ${selector}`); return found; };
const button = (label) => { const found = [...host.querySelectorAll('button')].find((node) => node.textContent.trim() === label); assert(found, `Missing button ${label}`); return found; };
const click = async (node) => act(async () => { node.click(); });
async function files(input, selected) {
  const transfer = new DataTransfer();
  selected.forEach((file) => transfer.items.add(file));
  await act(async () => { input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function mount() {
  root = createRoot(host);
  await act(async () => { root.render(<LibraryPage screen="downloads" navigate={() => {}} />); });
}
async function test(name, action) {
  media = ['one', 'two', 'three'].map(makeVideo);
  calls = []; failures = new Set(); thumbnailFailure = false;
  try { await action(); report.push({ name, passed: true }); }
  catch (error) { report.push({ name, passed: false }); console.error(error); }
  finally { if (root) await act(async () => root.unmount()); root = null; }
  document.getElementById('results').textContent = report.map((result) => `${result.passed ? 'PASS' : 'FAIL'} ${result.name}`).join('\n');
}

await test('Batch conversion only queues selected media and retains failed selections for retry', async () => {
  await mount();
  await click(find('[aria-label="Select Video one"]'));
  await click(find('[aria-label="Select Video three"]'));
  failures.add('three');
  await click(button('Convert selected to MP3'));
  const conversions = calls.filter((call) => call.path.endsWith('/conversions/mp3'));
  assert(conversions.length === 2 && conversions.every((call) => !call.path.includes('/two/')), 'Only chosen media converted');
  assert(!find('[aria-label="Select Video one"]').checked && find('[aria-label="Select Video three"]').checked, 'Only failed selection remains');
  assert(host.textContent.includes('Conversion rejected'), 'Batch failure visible');
  failures.clear();
  await click(button('Convert selected to MP3'));
  assert(!find('[aria-label="Select Video three"]').checked, 'Successful retry clears selection');
});

await test('Select all follows the visible source filter', async () => {
  media[2].connector = 'rumble';
  await mount();
  await click(button('YouTube'));
  await click(find('[aria-label="Select all visible"]'));
  await click(button('Convert selected to MP3'));
  const conversions = calls.filter((call) => call.path.endsWith('/conversions/mp3'));
  assert(conversions.length === 2 && conversions.every((call) => !call.path.includes('/three/')), 'Hidden source excluded');
});

await test('Uploads send actual files and artwork and show both audio and video in Downloads', async () => {
  await mount();
  const audio = new File(['audio'], 'custom.mp3', { type: 'audio/mpeg' });
  const video = new File(['video'], 'custom.mkv', { type: 'video/x-matroska' });
  const thumbnail = new File(['art'], 'art.png', { type: 'image/png' });
  await files(find('.media-upload-panel input[multiple]'), [audio, video]);
  await files(find('.media-upload-panel input[accept]'), [thumbnail]);
  await click(button('Upload media'));
  const uploads = calls.filter((call) => call.path === '/api/media/upload');
  assert(uploads.length === 2 && uploads.every((call) => call.body instanceof FormData && call.body.get('thumbnail').name === 'art.png'), 'Multipart media and shared artwork');
  assert(find('[aria-label="Select custom.mp3"]') && find('[aria-label="Select custom.mkv"]'), 'Both upload kinds listed');
  assert(host.textContent.includes('Audio with thumbnail'), 'Audio type visible');
  const audioCard = find('[aria-label="Select custom.mp3"]').closest('.media-card');
  const renderButton = audioCard.querySelector('[aria-label="Create MP4 with thumbnail"]');
  assert(renderButton && !renderButton.disabled, 'Audio with artwork offers MP4 rendering');
  await click(renderButton);
  assert(calls.some((call) => call.path.endsWith('/conversions/mp4')), 'MP4 rendering request sent');
});

await test('Audio without artwork requires a custom thumbnail before MP4 rendering', async () => {
  media[0] = { ...media[0], media_kind: 'audio', connector: 'upload', thumbnail_url: null,
    conversions: { mp3: { format: 'mp3', status: 'completed', stream_url: '/one.mp3', download_url: '/one.mp3' } } };
  await mount();
  const renderButton = find('[aria-label="Create MP4 with thumbnail"]');
  assert(renderButton.disabled && renderButton.title.includes('thumbnail'), 'Missing artwork explains disabled conversion');
});

await test('Upload retry sends only failed files', async () => {
  failures.add('retry.mp4');
  await mount();
  await files(find('.media-upload-panel input[multiple]'), [new File(['ok'], 'ok.mp3'), new File(['retry'], 'retry.mp4')]);
  await click(button('Upload media'));
  assert(host.textContent.includes('Upload rejected'), 'Upload error visible');
  failures.clear();
  await click(button('Upload media'));
  const uploads = calls.filter((call) => call.path === '/api/media/upload');
  assert(uploads.length === 3 && uploads[2].body.get('file').name === 'retry.mp4', 'Already uploaded file not duplicated');
});

await test('Custom thumbnail updates the card; failed replacement keeps existing art', async () => {
  await mount();
  const thumbnail = new File(['art'], 'cover.png', { type: 'image/png' });
  await files(find('[aria-label="Custom thumbnail for Video one"]'), [thumbnail]);
  assert(find('[aria-label="Play Video one"] img').getAttribute('src').includes('?v=2'), 'New thumbnail shown');
  thumbnailFailure = true;
  await files(find('[aria-label="Custom thumbnail for Video one"]'), [thumbnail]);
  assert(host.textContent.includes('Thumbnail rejected'), 'Thumbnail error visible');
  assert(find('[aria-label="Play Video one"] img').getAttribute('src').includes('?v=2'), 'Existing art retained');
});

const passed = report.filter((result) => result.passed).length;
document.getElementById('results').textContent += `\n${passed}/${report.length} passed`;
document.body.dataset.testStatus = passed === report.length ? 'passed' : 'failed';
