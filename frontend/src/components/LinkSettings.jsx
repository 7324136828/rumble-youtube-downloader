import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { deleteRecordedLink, getLinkSettings, updateLinkSettings, updateLinkState } from '../services/api';
import Icon from './Icon';

const FILTERS = [
  ['all', 'All recorded links'],
  ['active', 'Unreviewed'],
  ['silenced', 'Marked repeated'],
  ['excluded', 'Excluded links'],
  ['allowed', 'Always allowed'],
];

const stateLabel = (state) => ({
  active: 'unreviewed',
  silenced: 'repeated',
  allowed: 'always allowed',
  excluded: 'excluded',
}[state] || state);

export default function LinkSettings() {
  const [settings, setSettings] = useState(null);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('all');
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const closeButton = useRef(null);

  const load = (state = filter, signal) => {
    setLoading(true);
    setError('');
    return getLinkSettings({ state }, signal).then((value) => {
      setSettings({ hide_repeated_links: value.hide_repeated_links });
      setItems(value.items || []);
      setTotal(value.total || 0);
    }).catch((err) => {
      if (!signal?.aborted) setError(err.message || 'Could not load link settings.');
    }).finally(() => { if (!signal?.aborted) setLoading(false); });
  };

  useEffect(() => {
    const controller = new AbortController();
    load('all', controller.signal);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    load(filter, controller.signal).then(() => closeButton.current?.focus());
    const onKey = (event) => { if (event.key === 'Escape' && !saving) setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => { controller.abort(); window.removeEventListener('keydown', onKey); };
  }, [open, filter]);

  const toggleRepeated = async (checked) => {
    setSaving(true); setError('');
    try {
      const value = await updateLinkSettings({ hide_repeated_links: checked });
      setSettings(value);
    } catch (err) { setError(err.message || 'Could not save link settings.'); }
    finally { setSaving(false); }
  };

  const changeState = async (item, state) => {
    setSaving(true); setError('');
    try {
      await updateLinkState(item.id, state);
      await load(filter);
    } catch (err) { setError(err.message || 'Could not update this link.'); }
    finally { setSaving(false); }
  };

  const remove = async (item) => {
    setSaving(true); setError('');
    try {
      await deleteRecordedLink(item.id);
      await load(filter);
    } catch (err) { setError(err.message || 'Could not delete this recorded link.'); }
    finally { setSaving(false); }
  };

  return <>
    <section className="download-settings-card link-settings-card" aria-labelledby="link-settings-heading">
      <div className="download-settings-heading"><span><Icon name="link" size={23} /></span><div><h2 id="link-settings-heading">Link settings</h2><p>Keep a local record of links shown by Search and Recommendations, then review or delete them yourself.</p></div></div>
      {settings && <div className="download-setting-row"><div><label htmlFor="hide-repeated-links">Hide links marked repeated</label><p id="hide-repeated-links-help">The app records links but does not decide which are repeats. Use Search, Recommendations, or the review window to mark them yourself.</p></div><input id="hide-repeated-links" type="checkbox" role="switch" checked={settings.hide_repeated_links} onChange={(event) => toggleRepeated(event.target.checked)} disabled={saving} aria-describedby="hide-repeated-links-help" /></div>}
      <div className="link-settings-actions"><button type="button" className="btn-secondary" onClick={() => setOpen(true)} disabled={loading && !settings}><Icon name="settings" size={16} />Manage recorded links</button>{total > 0 && <span>{total} recorded</span>}</div>
      {!open && error && <p className="search-download-error" role="alert">{error}</p>}
    </section>
    {open && createPortal(<div className="retention-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setOpen(false); }}>
      <section className="retention-modal link-settings-modal" role="dialog" aria-modal="true" aria-labelledby="managed-links-heading">
        <header><div><p className="eyebrow">HUMAN REVIEW</p><h2 id="managed-links-heading">Recorded links</h2><p className="retention-video-title">New links remain unreviewed. Only you can mark them repeated, always allowed, or excluded, and you can delete any record.</p></div><button ref={closeButton} className="icon-btn" type="button" aria-label="Close link settings" disabled={saving} onClick={() => setOpen(false)}><Icon name="close" size={18} /></button></header>
        <div className="managed-links-controls"><label htmlFor="managed-link-filter">Show</label><select id="managed-link-filter" value={filter} onChange={(event) => setFilter(event.target.value)} disabled={saving}>{FILTERS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></div>
        {error && <div className="error-banner" role="alert"><span>{error}</span></div>}
        <div className="managed-links-list">
          {loading ? <p className="muted" role="status">Loading links...</p> : items.length === 0 ? <p className="muted">No links in this view.</p> : items.map((item) => <article className="managed-link" key={item.id}><div><strong>{item.title || item.source_url}</strong><a href={item.source_url} target="_blank" rel="noopener noreferrer">{item.source_url}</a><span>{item.provider} · shown {item.seen_count} time{item.seen_count === 1 ? '' : 's'} · {stateLabel(item.state)}</span></div><div className="managed-link-actions">{item.state !== 'active' && <button type="button" className="btn-secondary" disabled={saving} onClick={() => changeState(item, 'active')}>Clear marking</button>}{item.state !== 'silenced' && <button type="button" className="btn-secondary" disabled={saving} onClick={() => changeState(item, 'silenced')}>Mark repeated</button>}{item.state !== 'allowed' && <button type="button" className="btn-secondary" disabled={saving} onClick={() => changeState(item, 'allowed')}>Allow link</button>}{item.state !== 'excluded' && <button type="button" className="btn-secondary" disabled={saving} onClick={() => changeState(item, 'excluded')}>Exclude link</button>}<button type="button" className="btn-secondary managed-link-delete" disabled={saving} onClick={() => remove(item)}>Delete record</button></div></article>)}
        </div>
        <footer><span className="muted small-text">{total} link{total === 1 ? '' : 's'} in this view</span><button type="button" className="btn-primary" onClick={() => setOpen(false)} disabled={saving}>Done</button></footer>
      </section>
    </div>, document.body)}
  </>;
}
