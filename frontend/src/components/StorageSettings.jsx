import React, { useEffect, useRef, useState } from 'react';
import { getStorageSettings, updateStorageSettings } from '../services/api';
import Icon from './Icon';
import './StorageSettings.css';

export default function StorageSettings() {
  const [settings, setSettings] = useState(null);
  const [folder, setFolder] = useState('');
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
    getStorageSettings(controller.signal).then((value) => {
      if (!controller.signal.aborted) {
        setSettings(value);
        setFolder(value.data_root || '');
      }
    }).catch((err) => {
      if (!controller.signal.aborted) setError(err.message || 'Could not load storage settings.');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { mounted.current = false; controller.abort(); };
  }, [retry]);

  const updateFolder = (value) => {
    setFolder(value);
    setNotice('');
  };
  const changed = settings && (folder.trim() || null) !== (settings.data_root || null);
  const save = async (event) => {
    event.preventDefault();
    if (!settings || saving || !changed) return;
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const value = await updateStorageSettings({ data_root: folder.trim() || null });
      if (mounted.current) {
        setSettings(value);
        setFolder(value.data_root || '');
        setNotice('Storage folder saved.');
      }
    } catch (err) {
      if (mounted.current) setError(err.message || 'Could not save storage settings.');
    } finally { if (mounted.current) setSaving(false); }
  };

  return <section className="download-settings-card storage-settings-card" aria-labelledby="storage-settings-heading">
    <div className="download-settings-heading"><span><Icon name="folder" size={23} /></span><div><h2 id="storage-settings-heading">Data storage</h2><p>Choose the folder for your library, downloads, and transcripts. Changing folders switches to that folder's library after restart. Existing media and files are not moved.</p></div></div>
    {loading && <p className="muted" role="status">Loading storage settings...</p>}
    {error && <div className="error-banner" role="alert"><span>{error}</span>{!settings && <button className="link-btn" type="button" onClick={() => setRetry((value) => value + 1)}>Retry storage settings</button>}</div>}
    {notice && <div className="success-banner" role="status"><Icon name="check" size={17} /><span>{notice}</span></div>}
    {settings && <>
      <dl className="storage-settings-paths"><div><dt>Active storage folder</dt><dd>{settings.active_data_root}</dd></div><div><dt>Default folder (%TEMP%\prod_jobs)</dt><dd>{settings.default_data_root}</dd></div></dl>
      {settings.restart_required && <div className="storage-settings-notice" role="status"><strong>Restart ClipFeed to use the saved storage folder.</strong><span>Folder after restart: <code>{settings.effective_data_root}</code></span></div>}
      {settings.environment_override && <p className="storage-settings-notice">JOBS_ROOT is set on the computer running ClipFeed and takes precedence over this preference. Your saved folder will apply when that override is removed and ClipFeed restarts.</p>}
      <form onSubmit={save}>
        <label htmlFor="storage-data-root">Storage folder</label>
        <p id="storage-folder-help" className="download-settings-note">Enter the full folder path on the computer running ClipFeed. Environment variables and ~ are supported. Leave blank to use the default folder.</p>
        <div className="storage-settings-input"><input id="storage-data-root" type="text" value={folder} onChange={(event) => updateFolder(event.target.value)} placeholder={settings.default_data_root} disabled={loading || saving} aria-describedby="storage-folder-help" autoComplete="off" spellCheck={false} /><button className="btn-secondary" type="button" onClick={() => updateFolder('')} disabled={loading || saving || !folder}>Use default</button></div>
        <p className="download-settings-note storage-settings-save-note">This preference is saved in <code>{settings.settings_path}</code>. Folder changes take effect after restarting ClipFeed.</p>
        <div className="download-settings-actions"><button className="btn-primary" type="submit" disabled={loading || saving || !changed}>{saving ? 'Saving storage folder...' : 'Save storage folder'}</button>{changed && !saving && <span className="muted small-text">Unsaved changes</span>}</div>
      </form>
    </>}
  </section>;
}
