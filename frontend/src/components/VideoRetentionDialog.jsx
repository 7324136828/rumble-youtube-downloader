import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { updateMediaRetention } from '../services/api';
import Icon from './Icon';
import './DownloadSettings.css';

export default function VideoRetentionDialog({ video, onClose, onSaved }) {
  const [days, setDays] = useState(String(video.retention_days > 0 ? video.retention_days : -1));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);
  const dialogRef = useRef(null);
  const requestRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const title = video.title || video.file_name || video.source_url;

  useEffect(() => {
    const trigger = document.activeElement;
    inputRef.current?.focus();
    inputRef.current?.select();
    const handleKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!requestRef.current) onCloseRef.current();
      } else if (event.key === 'Tab') {
        const controls = [...dialogRef.current.querySelectorAll('button:not(:disabled), input:not(:disabled)')];
        if (!controls.length) { event.preventDefault(); return; }
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || !controls.includes(document.activeElement))) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !controls.includes(document.activeElement))) {
          event.preventDefault(); first.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      requestRef.current?.abort();
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);

  const close = () => { if (!requestRef.current) onClose(); };
  const save = async (event) => {
    event.preventDefault();
    if (requestRef.current) return;
    const value = Number(days);
    if (!days.trim() || !Number.isInteger(value) || value === 0 || value > 3650) {
      setError('Enter 1 to 3650 days, or a negative whole number to keep this video indefinitely.');
      return;
    }
    const controller = new AbortController();
    requestRef.current = controller;
    setSaving(true); setError('');
    try {
      const updated = await updateMediaRetention(video.id, value, controller.signal);
      if (!controller.signal.aborted) onSaved(updated);
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message || 'Could not save this video’s expiration.');
    } finally {
      if (!controller.signal.aborted) { requestRef.current = null; setSaving(false); }
    }
  };

  return createPortal(<div className="retention-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialogRef} className="retention-modal" role="dialog" aria-modal="true" aria-labelledby="video-retention-modal-heading" aria-describedby="video-retention-modal-title">
      <header><div><p className="eyebrow">THIS VIDEO</p><h2 id="video-retention-modal-heading">Video expiration</h2><p id="video-retention-modal-title" className="retention-video-title">{title}</p></div><button className="icon-btn" type="button" aria-label="Close video expiration settings" disabled={saving} onClick={close}><Icon name="close" size={18} /></button></header>
      <form onSubmit={save} noValidate>
        <label htmlFor="video-retention-modal-days">Expire this video after</label>
        <div className="retention-days-input"><input ref={inputRef} id="video-retention-modal-days" type="number" max="3650" step="1" value={days} onChange={(event) => setDays(event.target.value)} disabled={saving} aria-describedby="video-retention-help" /><span>days</span></div>
        <p id="video-retention-help">Days are counted from when this download finished. Enter any negative whole number, such as <strong>-1</strong>, to keep this video indefinitely. Zero is not valid.</p>
        <p>This choice takes priority over the default expiration setting. For an unfinished download, the countdown starts when it completes.</p>
        <p>Videos past their expiration are removed during cleanup, which runs hourly and when the library is refreshed.</p>
        {error && <div className="error-banner" role="alert">{error}</div>}
        <footer><button className="btn-secondary" type="button" disabled={saving} onClick={close}>Cancel</button><button className="btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save expiration'}</button></footer>
      </form>
    </section>
  </div>, document.body);
}
