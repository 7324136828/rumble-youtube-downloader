import React, { useEffect, useId, useRef, useState } from 'react';
import { getMediaTranscript, startMediaTranscript } from '../services/api';
import Icon from './Icon';
import './TranscriptActions.css';

const EMPTY = { status: 'idle', source: null, language: null, error_message: null };
const ACTIVE = new Set(['queued', 'fetching', 'transcribing']);
const statusLabel = (status) => ({ queued: 'Transcript queued...', fetching: 'Checking source captions...', transcribing: 'Generating with Whisper...' })[status];

export default function TranscriptActions({ video, onMediaUpdated, showText = false }) {
  const [transcript, setTranscript] = useState(() => ({ ...EMPTY, ...video?.transcript }));
  const [text, setText] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [requestError, setRequestError] = useState('');
  const panelId = useId();
  const currentId = useRef(video?.id);
  const currentVideo = useRef(video);
  const updateCallback = useRef(onMediaUpdated);
  const mounted = useRef(true);
  const requesting = useRef(false);
  const readController = useRef(null);
  currentId.current = video?.id;
  currentVideo.current = video;
  updateCallback.current = onMediaUpdated;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; readController.current?.abort(); };
  }, []);
  useEffect(() => {
    setText(null);
    setExpanded(false);
    setLoading(false);
    setStarting(false);
    setRequestError('');
    requesting.current = false;
    readController.current?.abort();
  }, [video?.id]);
  useEffect(() => {
    setTranscript({ ...EMPTY, ...video?.transcript });
    if (video?.transcript?.status !== 'completed') setText(null);
  }, [video?.id, video?.transcript]);

  const isCurrent = (id) => mounted.current && id === currentId.current;
  const applyTranscript = (id, next) => {
    if (!isCurrent(id)) return;
    const { text: content, ...metadata } = next;
    setTranscript({ ...EMPTY, ...metadata });
    if (typeof content === 'string') setText(content);
    else if (metadata.status !== 'completed') setText(null);
    updateCallback.current?.({ ...currentVideo.current, transcript: metadata });
  };
  const read = async () => {
    const id = currentId.current;
    if (!id || requesting.current) return;
    requesting.current = true;
    const controller = new AbortController();
    readController.current = controller;
    setLoading(true);
    try {
      const next = await getMediaTranscript(id, controller.signal);
      if (isCurrent(id)) { applyTranscript(id, next); setRequestError(''); }
    } catch (error) {
      if (isCurrent(id) && error.name !== 'AbortError') setRequestError(error.message || 'Could not load the transcript.');
    } finally {
      if (isCurrent(id)) { requesting.current = false; setLoading(false); }
    }
  };

  const working = ACTIVE.has(transcript.status);
  useEffect(() => {
    if (!working || !video?.id) return undefined;
    const timer = setInterval(() => { if (!starting) read(); }, 1000);
    return () => clearInterval(timer);
  }, [working, video?.id, starting]);

  const start = async () => {
    const id = currentId.current;
    if (!id || starting || working || requesting.current) return;
    const previous = transcript;
    setStarting(true);
    setRequestError('');
    setTranscript({ ...EMPTY, status: 'queued' });
    try { applyTranscript(id, await startMediaTranscript(id)); }
    catch (error) {
      if (isCurrent(id)) {
        setTranscript(previous);
        setRequestError(error.message || 'Could not start the transcript.');
      }
    } finally { if (isCurrent(id)) setStarting(false); }
  };
  const toggle = () => {
    const open = !expanded;
    setExpanded(open);
    if (open && (text === null || transcript.status !== 'completed')) read();
  };
  const complete = transcript.status === 'completed';
  const failure = requestError || transcript.error_message;
  const sourceLabel = transcript.source === 'subtitles' ? 'Source captions' : transcript.source === 'whisper' ? 'Generated with Whisper' : 'Transcript ready';

  return <div className={`media-transcript${showText ? ' media-transcript--detail' : ''}`}>
    <div className="media-transcript-actions">
      {showText && <button type="button" className="media-transcript-button" onClick={toggle} aria-expanded={expanded} aria-controls={panelId}><Icon name="info" size={16} />{expanded ? 'Hide transcript' : 'Show transcript'}</button>}
      {complete ? <>
        {transcript.download_url && <a className="media-transcript-button is-complete" href={transcript.download_url} download aria-label="Download transcript as TXT"><Icon name="download" size={15} />Download transcript (.txt)</a>}
        <span className="media-transcript-source">{sourceLabel}{transcript.language ? ` · ${transcript.language}` : ''}</span>
      </> : working || starting ? <span className="media-transcript-progress" role="status"><Icon name="refresh" size={16} />{statusLabel(transcript.status) || 'Transcript queued...'}</span>
        : <button type="button" className="media-transcript-button" onClick={start} disabled={loading} title={video?.connector === 'upload' ? 'Generate a transcript from this file with Whisper' : 'Download source captions when available, otherwise generate with Whisper'}><Icon name="info" size={16} />{transcript.status === 'failed' ? 'Retry transcript' : 'Get transcript'}</button>}
    </div>
    {failure && <div className="media-transcript-error" role="alert">{failure}{requestError && complete && expanded && <button type="button" className="media-transcript-button" disabled={loading} onClick={read}>Retry loading transcript</button>}</div>}
    {showText && expanded && <section id={panelId} className="media-transcript-panel" aria-label="Video transcript">
      <h4>Transcript</h4>
      {loading && text === null ? <p role="status">Loading transcript...</p>
        : complete && text !== null ? <div className="media-transcript-text">{text || 'This transcript has no spoken text.'}</div>
          : complete ? <p>The transcript could not be loaded.</p>
            : working ? <p>{statusLabel(transcript.status)} The transcript will appear here when it is ready.</p>
              : transcript.status === 'failed' ? <p>Use Retry transcript to try again.</p>
                : <p>No transcript yet. Get transcript {video?.connector === 'upload' ? 'to generate one with Whisper.' : 'to download available captions or generate one with Whisper.'}</p>}
    </section>}
  </div>;
}
