import React from 'react';

export const DEFAULT_OPTIONS = {
  model: 'turbo',
  language: '',
  device: 'auto',
  compute_type: '',
  workers: 1,
  chunk_seconds: 30,
  gpu_indices: '',
  impersonate: '',
  keep_video: false,
  allow_video_fallback: false,
  audio_only: false,
  no_transcript: false,
  overwrite_transcript: false,
  no_vad: false,
  verbose: false,
};

export function buildPayload(options) {
  const payload = {};
  if (options.model) payload.model = options.model;
  if (options.language.trim()) payload.language = options.language.trim();
  if (options.device && options.device !== 'auto') payload.device = options.device;
  if (options.compute_type) payload.compute_type = options.compute_type;
  if (Number(options.workers) > 1) payload.workers = Number(options.workers);
  if (Number(options.chunk_seconds) !== 30)
    payload.chunk_seconds = Number(options.chunk_seconds);
  const indices = options.gpu_indices
    .split(/[\s,]+/)
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n >= 0);
  if (indices.length && JSON.stringify(indices) !== '[0]')
    payload.gpu_indices = indices;
  if (options.impersonate.trim()) payload.impersonate = options.impersonate.trim();
  for (const flag of [
    'keep_video',
    'allow_video_fallback',
    'audio_only',
    'no_transcript',
    'overwrite_transcript',
    'no_vad',
    'verbose',
  ]) {
    if (options[flag]) payload[flag] = true;
  }
  return payload;
}

export default function OptionsPanel({ options, onChange }) {
  const set = (key) => (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    onChange({ ...options, [key]: value });
  };

  return (
    <details className="options-panel">
      <summary>Conversion settings</summary>
      <div className="options-grid">
        <label>
          Whisper model
          <select value={options.model} onChange={set('model')}>
            <option value="turbo">turbo (default)</option>
            <option value="large-v3">large-v3</option>
            <option value="medium">medium</option>
            <option value="small">small</option>
            <option value="base">base</option>
            <option value="tiny">tiny</option>
          </select>
        </label>
        <label>
          Language (blank = auto)
          <input
            type="text"
            placeholder="en"
            value={options.language}
            onChange={set('language')}
          />
        </label>
        <label>
          Device
          <select value={options.device} onChange={set('device')}>
            <option value="auto">auto</option>
            <option value="cpu">cpu</option>
            <option value="cuda">cuda</option>
          </select>
        </label>
        <label>
          Compute type
          <select value={options.compute_type} onChange={set('compute_type')}>
            <option value="">auto</option>
            <option value="float16">float16</option>
            <option value="float32">float32</option>
          </select>
        </label>
        <label>
          Workers
          <input
            type="number"
            min="1"
            max="8"
            value={options.workers}
            onChange={set('workers')}
          />
        </label>
        <label>
          Chunk seconds
          <input
            type="number"
            min="1"
            max="120"
            value={options.chunk_seconds}
            onChange={set('chunk_seconds')}
          />
        </label>
        <label>
          GPU indices (e.g. "0 1")
          <input
            type="text"
            placeholder="0"
            value={options.gpu_indices}
            onChange={set('gpu_indices')}
          />
        </label>
        <label>
          Impersonate browser
          <input
            type="text"
            placeholder="chrome"
            value={options.impersonate}
            onChange={set('impersonate')}
          />
        </label>
      </div>
      <div className="options-flags">
        <label>
          <input type="checkbox" checked={options.keep_video} onChange={set('keep_video')} />
          Keep video (enables web playback)
        </label>
        <label>
          <input
            type="checkbox"
            checked={options.allow_video_fallback}
            onChange={set('allow_video_fallback')}
          />
          Allow video fallback
        </label>
        <label>
          <input type="checkbox" checked={options.audio_only} onChange={set('audio_only')} />
          Audio only
        </label>
        <label>
          <input
            type="checkbox"
            checked={options.no_transcript}
            onChange={set('no_transcript')}
          />
          No transcript
        </label>
        <label>
          <input type="checkbox" checked={options.no_vad} onChange={set('no_vad')} />
          Disable VAD
        </label>
        <label>
          <input
            type="checkbox"
            checked={options.overwrite_transcript}
            onChange={set('overwrite_transcript')}
          />
          Overwrite transcript cache
        </label>
        <label>
          <input type="checkbox" checked={options.verbose} onChange={set('verbose')} />
          Verbose
        </label>
      </div>
    </details>
  );
}
