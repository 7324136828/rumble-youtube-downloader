import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { updateMediaRetention, updateWatchLaterRetention } from '../services/api';
import Icon from './Icon';
import './DownloadSettings.css';

export default function VideoRetentionDialog({ video, onClose, onSaved, watchLater = false, defaultPolicy = false, onUpdate }) {
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
      const updated = await (onUpdate ? onUpdate(value, controller.signal) : watchLater
        ? updateWatchLaterRetention(video.catalog_id, value, controller.signal)
        : updateMediaRetention(video.id, value, controller.signal));
      if (!controller.signal.aborted) onSaved(updated);
    } catch (err) {
      if (!controller.signal.aborted) setError(err.message || 'Could not save this video’s expiration.');
    } finally {
      if (!controller.signal.aborted) { requestRef.current = null; setSaving(false); }
    }
  };

  return createPortal(<div className="retention-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialogRef} className="retention-modal" role="dialog" aria-modal="true" aria-labelledby="video-retention-modal-heading" aria-describedby="video-retention-modal-title">
      <header><div><p className="eyebrow">{watchLater ? 'WATCH LATER' : 'THIS VIDEO'}</p><h2 id="video-retention-modal-heading">{watchLater ? defaultPolicy ? 'Default Watch later expiration' : 'Watch later expiration' : 'Video expiration'}</h2><p id="video-retention-modal-title" className="retention-video-title">{title}</p></div><button className="icon-btn" type="button" aria-label="Close video expiration settings" disabled={saving} onClick={close}><Icon name="close" size={18} /></button></header>
      <form onSubmit={save} noValidate>
        <label htmlFor="video-retention-modal-days">{watchLater ? 'Expire Watch later entries after' : 'Expire this video after'}</label>
        <div className="retention-days-input"><input ref={inputRef} id="video-retention-modal-days" type="number" max="3650" step="1" value={days} onChange={(event) => setDays(event.target.value)} disabled={saving} aria-describedby="video-retention-help" /><span>days</span></div>
        <p id="video-retention-help">{watchLater ? 'Days are counted from when the entry was first saved. Entries already saved before this feature start their countdown on upgrade.' : 'Days are counted from when this download finished.'} Enter any negative whole number, such as <strong>-1</strong>, to keep {watchLater ? 'saved entries' : 'this video'} indefinitely. Zero is not valid.</p>
        <p>{watchLater ? defaultPolicy ? 'This default applies to current and future entries that have no individual expiration setting. Entries with their own setting keep that choice.' : 'This choice takes priority over the default Watch later expiration setting.' : 'This choice takes priority over the default expiration setting. For an unfinished download, the countdown starts when it completes.'}</p>
        <p>{watchLater ? 'Expired entries and their temporary thumbnails are removed hourly and when Watch later is refreshed. Downloaded files follow their separate Downloads expiration settings.' : 'Videos past their expiration are removed during cleanup, which runs hourly and when the library is refreshed.'}</p>
        {error && <div className="error-banner" role="alert">{error}</div>}
        <footer><button className="btn-secondary" type="button" disabled={saving} onClick={close}>Cancel</button><button className="btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save expiration'}</button></footer>
      </form>
    </section>
  </div>, document.body);
}
