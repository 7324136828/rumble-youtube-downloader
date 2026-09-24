import React, { useEffect, useRef, useState } from 'react';
import { getDownloadSettings, updateDownloadSettings } from '../services/api';
import Icon from './Icon';
import './DownloadSettings.css';

export default function RetentionSettingsButton({ onSaved }) {
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState('7');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  const show = async () => {
    setOpen(true); setLoading(true); setError('');
    try {
      const settings = await getDownloadSettings();
      setDays(String(settings.retention_days));
    } catch (err) { setError(err.message || 'Could not load expiration settings.'); }
    finally { setLoading(false); }
  };
  const close = () => { if (!saving) setOpen(false); };
  useEffect(() => {
    if (!open) return undefined;
    const escape = (event) => { if (event.key === 'Escape') close(); };
    document.addEventListener('keydown', escape);
    if (!loading) inputRef.current?.focus();
    return () => document.removeEventListener('keydown', escape);
  }, [open, loading, saving]);
  const save = async (event) => {
    event.preventDefault();
    const value = Number(days);
    if (!Number.isInteger(value) || value === 0 || value > 3650) {
      setError('Enter 1 to 3650 days, or any negative whole number to keep videos indefinitely.');
      return;
    }
    setSaving(true); setError('');
    try {
      const settings = await updateDownloadSettings({ retention_days: value });
      setDays(String(settings.retention_days));
      setOpen(false);
      onSaved?.(settings);
    } catch (err) { setError(err.message || 'Could not save expiration settings.'); }
    finally { setSaving(false); }
  };

  return <>
    <button className="btn-secondary retention-settings-button" type="button" onClick={show}><Icon name="settings" size={15} />Expiration settings</button>
    {open && <div className="retention-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <section className="retention-modal" role="dialog" aria-modal="true" aria-labelledby="retention-modal-heading">
        <header><div><p className="eyebrow">LOCAL STORAGE</p><h2 id="retention-modal-heading">Video expiration</h2></div><button className="icon-btn" type="button" aria-label="Close expiration settings" disabled={saving} onClick={close}><Icon name="close" size={18} /></button></header>
        <form onSubmit={save}>
          <label htmlFor="retention-modal-days">Expire downloaded videos after</label>
          <div className="retention-days-input"><input ref={inputRef} id="retention-modal-days" type="number" max="3650" step="1" value={days} onChange={(event) => setDays(event.target.value)} disabled={loading || saving} /><span>days</span></div>
          <p>Expiration is calculated from when each download finished. Enter a negative number, such as <strong>-1</strong>, to keep videos indefinitely. Zero is not valid.</p>
          <p>The new value is applied to completed and in-progress downloads that use the default. Videos with their own expiration setting keep that choice. An hourly background job removes files after they expire.</p>
          {loading && <p className="muted" role="status">Loading expiration settings…</p>}
          {error && <div className="error-banner" role="alert">{error}</div>}
          <footer><button className="btn-secondary" type="button" disabled={saving} onClick={close}>Cancel</button><button className="btn-primary" disabled={loading || saving}>{saving ? 'Saving…' : 'Save expiration'}</button></footer>
        </form>
      </section>
    </div>}
  </>;
}
