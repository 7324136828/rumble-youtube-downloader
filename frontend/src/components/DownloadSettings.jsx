import React, { useEffect, useRef, useState } from 'react';
import { getDownloadSettings, updateDownloadSettings } from '../services/api';
import Icon from './Icon';
import LinkSettings from './LinkSettings';
import PlaybackSettings from './PlaybackSettings';
import './DownloadSettings.css';

const withCookieDefaults = (value) => ({
  ...value,
  cookie_browser: value.cookie_browser || '',
  cookie_browser_profile: value.cookie_browser_profile || '',
  cookie_file: value.cookie_file || '',
});

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
      if (!controller.signal.aborted) {
        const normalized = withCookieDefaults(value);
        setSettings(normalized);
        setSaved(normalized);
      }
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
    const retentionDays = Number(settings.retention_days);
    if (!Number.isInteger(retentionDays) || retentionDays === 0 || retentionDays > 3650) {
      setError('Enter 1 to 3650 days, or any negative whole number to keep videos indefinitely.');
      return;
    }
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const value = await updateDownloadSettings({
        convert_for_browser: settings.convert_for_browser,
        generate_thumbnails: settings.generate_thumbnails,
        retention_days: retentionDays,
        cookie_browser: settings.cookie_browser,
        cookie_browser_profile: settings.cookie_browser ? (settings.cookie_browser_profile || '').trim() : '',
        cookie_file: (settings.cookie_file || '').trim(),
      });
      if (mounted.current) {
        const normalized = withCookieDefaults(value);
        setSettings(normalized);
        setSaved(normalized);
        setNotice('Download settings saved. Expiration changes apply to current and future downloads that use the default.');
      }
    } catch (err) {
      if (mounted.current) setError(err.message || 'Could not save download settings.');
    } finally { if (mounted.current) setSaving(false); }
  };
  const changed = settings && saved && Object.keys(settings).some((key) => settings[key] !== saved[key]);

  return <div className="page-wrap download-settings">
    <div className="page-heading"><div><p className="eyebrow">YOUR DOWNLOADS, YOUR CHOICE</p><h1>Download settings<span className="accent">.</span></h1><p className="page-description">Choose what happens after a video finishes downloading.</p></div></div>
    <PlaybackSettings />
    {loading && <p className="muted" role="status">Loading download settings...</p>}
    {error && <div className="error-banner" role="alert"><span>{error}</span>{!settings && <button className="link-btn" onClick={() => setRetry((value) => value + 1)}>Retry loading</button>}</div>}
    {notice && <div className="success-banner" role="status"><Icon name="check" size={17} /><span>{notice}</span></div>}
    {settings && <form className="download-settings-card" onSubmit={save}>
      <div className="download-settings-heading"><span><Icon name="settings" size={23} /></span><div><h2>After downloading</h2><p>Processing choices apply to new downloads. Expiration changes update completed and in-progress downloads that use the default. Individual video expiration settings take priority.</p></div></div>
      <div className="download-setting-row"><div><label htmlFor="download-convert-for-browser">Convert downloads to MP4</label><p id="download-convert-help">Off by default. WebM plays directly in Watch and Swipe using your browser. Enable this to convert new downloads to H.264/AAC MP4 when needed; conversion can take time. If your browser cannot play an original file, enable MP4 conversion for your next download or use an external player.</p></div><input id="download-convert-for-browser" type="checkbox" role="switch" checked={settings.convert_for_browser} onChange={(event) => update('convert_for_browser', event.target.checked)} disabled={loading || saving} aria-describedby="download-convert-help" /></div>
      <div className="download-setting-row"><div><label htmlFor="download-generate-thumbnails">Generate missing thumbnails</label><p id="download-thumbnails-help">Create a preview image when the source has no thumbnail. Thumbnails supplied by YouTube, Rumble, or another source are kept either way.</p></div><input id="download-generate-thumbnails" type="checkbox" role="switch" checked={settings.generate_thumbnails} onChange={(event) => update('generate_thumbnails', event.target.checked)} disabled={loading || saving} aria-describedby="download-thumbnails-help" /></div>
      <div className="download-setting-row download-cookie-row"><div><label htmlFor="download-cookie-browser">Browser cookies for protected videos</label><p id="download-cookie-browser-help">If YouTube asks you to confirm you are not a bot, choose the browser where you are signed in. ClipFeed asks yt-dlp to read that browser's local cookies; cookie values are never stored in ClipFeed's database or returned by its API.</p><p className="download-cookie-help">On Windows, Edge can keep its cookie database locked through Startup boost or background apps, even after its windows close. Edge shares Chromium's cookie reader, so its errors can say "Chrome". If closing Edge fully does not help, use an exported cookie file below.</p></div><div className="download-cookie-fields"><select id="download-cookie-browser" value={settings.cookie_browser} onChange={(event) => update('cookie_browser', event.target.value)} disabled={loading || saving} aria-describedby="download-cookie-browser-help"><option value="">Do not use browser cookies</option><option value="chrome">Google Chrome</option><option value="edge">Microsoft Edge</option><option value="firefox">Mozilla Firefox</option><option value="brave">Brave</option><option value="chromium">Chromium</option><option value="opera">Opera</option><option value="vivaldi">Vivaldi</option><option value="whale">Whale</option><option value="safari">Safari</option></select>{settings.cookie_browser && <><label htmlFor="download-cookie-profile">Profile name or path <span className="muted">(optional)</span></label><input id="download-cookie-profile" type="text" maxLength="500" value={settings.cookie_browser_profile} onChange={(event) => update('cookie_browser_profile', event.target.value)} disabled={loading || saving} placeholder="Most recently used profile" /></>}</div></div>
      <div className="download-setting-row download-cookie-row"><div><label htmlFor="download-cookie-file">Exported cookies file <span className="muted">(optional)</span></label><p id="download-cookie-file-help">Use a Netscape-format cookies.txt file to download while Edge stays open. Enter its full path on the computer running ClipFeed. This file takes priority over browser cookies; your browser and profile choices are kept. Clear the path and save to return to your usual cookie source.</p><p className="download-cookie-help">Only the path is saved. ClipFeed reads the file without rewriting it. Keep the file private. <a href="https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies" target="_blank" rel="noreferrer">How to export YouTube cookies</a></p></div><div className="download-cookie-fields"><input id="download-cookie-file" type="text" maxLength="2048" value={settings.cookie_file} onChange={(event) => update('cookie_file', event.target.value)} disabled={loading || saving} aria-describedby="download-cookie-file-help" placeholder="C:\Private\youtube-cookies.txt" autoComplete="off" spellCheck={false} /></div></div>
      <div className="download-setting-row retention-days-row"><div><label htmlFor="download-retention-days">Days to keep each video</label><p id="download-retention-help">The default is 7 days, counted from download completion. Enter any negative number to keep videos indefinitely. Zero is not valid. Expired files are checked hourly; watch-history metadata remains available.</p></div><input id="download-retention-days" type="number" max="3650" step="1" value={settings.retention_days} onChange={(event) => update('retention_days', event.target.value)} disabled={loading || saving} aria-describedby="download-retention-help" /></div>
      <p className="download-settings-note">Combining separate video and audio tracks still runs when required, so your download includes both picture and sound.</p>
      <div className="download-settings-actions"><button className="btn-primary" type="submit" disabled={loading || saving || !changed}>{saving ? 'Saving settings...' : 'Save settings'}</button>{changed && !saving && <span className="muted small-text">Unsaved changes</span>}</div>
    </form>}
    <LinkSettings />
  </div>;
}
