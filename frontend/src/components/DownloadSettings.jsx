import React, { useEffect, useRef, useState } from 'react';
import { getDownloadSettings, updateDownloadSettings } from '../services/api';
import Icon from './Icon';
import './DownloadSettings.css';

export default function DownloadSettings() {
  const [settings, setSettings] = useState(null);
  const [saved, setSaved] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    getDownloadSettings(controller.signal).then((value) => {
      if (!controller.signal.aborted) { setSettings(value); setSaved(value); }
    }).catch((err) => {
      if (!controller.signal.aborted) setError(err.message || 'Could not load download settings.');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { mounted.current = false; controller.abort(); };
  }, [retry]);

  const update = (key, value) => {
    setSettings((previous) => ({ ...previous, [key]: value }));
    setNotice('');
  };
  const save = async (event) => {
    event.preventDefault();
    if (!settings || saving) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const value = await updateDownloadSettings({ convert_for_browser: settings.convert_for_browser, generate_thumbnails: settings.generate_thumbnails });
      if (mounted.current) {
        setSettings(value);
        setSaved(value);
        setNotice('Download settings saved. These settings apply to new downloads.');
      }
    } catch (err) {
      if (mounted.current) setError(err.message || 'Could not save download settings.');
    } finally { if (mounted.current) setSaving(false); }
  };
  const changed = settings && saved && (settings.convert_for_browser !== saved.convert_for_browser || settings.generate_thumbnails !== saved.generate_thumbnails);

  return <div className="page-wrap download-settings">
    <div className="page-heading"><div><p className="eyebrow">YOUR DOWNLOADS, YOUR CHOICE</p><h1>Download settings<span className="accent">.</span></h1><p className="page-description">Choose what happens after a video finishes downloading.</p></div></div>
    {loading && <p className="muted" role="status">Loading download settings...</p>}
    {error && <div className="error-banner" role="alert"><span>{error}</span>{!settings && <button className="link-btn" onClick={() => setRetry((value) => value + 1)}>Retry loading</button>}</div>}
    {notice && <div className="success-banner" role="status"><Icon name="check" size={17} /><span>{notice}</span></div>}
    {settings && <form className="download-settings-card" onSubmit={save}>
      <div className="download-settings-heading"><span><Icon name="settings" size={23} /></span><div><h2>After downloading</h2><p>Settings apply to new downloads. Downloads already in progress keep their current settings.</p></div></div>
      <div className="download-setting-row"><div><label htmlFor="download-convert-for-browser">Convert downloads to MP4</label><p id="download-convert-help">Off by default. WebM plays directly in Watch and Swipe using your browser. Enable this to convert new downloads to H.264/AAC MP4 when needed; conversion can take time. If your browser cannot play an original file, enable MP4 conversion for your next download or use an external player.</p></div><input id="download-convert-for-browser" type="checkbox" role="switch" checked={settings.convert_for_browser} onChange={(event) => update('convert_for_browser', event.target.checked)} disabled={loading || saving} aria-describedby="download-convert-help" /></div>
      <div className="download-setting-row"><div><label htmlFor="download-generate-thumbnails">Generate missing thumbnails</label><p id="download-thumbnails-help">Create a preview image when the source has no thumbnail. Thumbnails supplied by YouTube, Rumble, or another source are kept either way.</p></div><input id="download-generate-thumbnails" type="checkbox" role="switch" checked={settings.generate_thumbnails} onChange={(event) => update('generate_thumbnails', event.target.checked)} disabled={loading || saving} aria-describedby="download-thumbnails-help" /></div>
      <p className="download-settings-note">Combining separate video and audio tracks still runs when required, so your download includes both picture and sound.</p>
      <div className="download-settings-actions"><button className="btn-primary" type="submit" disabled={loading || saving || !changed}>{saving ? 'Saving settings...' : 'Save settings'}</button>{changed && !saving && <span className="muted small-text">Unsaved changes</span>}</div>
    </form>}
  </div>;
}
