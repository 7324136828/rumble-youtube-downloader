import React, { useEffect, useRef, useState } from 'react';
import { connectDownloader, getConnectorStatus, updateConnectorSettings } from '../services/api';
import Icon from './Icon';
import './ConnectorIntegrationSettings.css';

export default function ConnectorIntegrationSettings() {
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState(null);
  const [baseUrl, setBaseUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(false);
  const mutation = useRef(0);
  const mutating = useRef(false);
  const initialized = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    let fetching = false;
    const refresh = async () => {
      if (mutating.current || fetching) return;
      fetching = true;
      const revision = mutation.current;
      if (!initialized.current) setLoading(true);
      try {
        const next = await getConnectorStatus(controller.signal);
        if (controller.signal.aborted || revision !== mutation.current) return;
        setStatus(next);
        if (!initialized.current) setBaseUrl(next.base_url || '');
        initialized.current = true;
        setError('');
      } catch (err) {
        if (!controller.signal.aborted && revision === mutation.current) setError(err.message || 'Could not load Connector status.');
      } finally {
        fetching = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [expanded]);

  const change = async (action) => {
    if (mutating.current) return;
    mutation.current += 1;
    mutating.current = true;
    setBusy(true);
    setError('');
    try {
      const next = await action();
      if (!mounted.current) return;
      setStatus(next);
      setBaseUrl(next.base_url || '');
      initialized.current = true;
    } catch (err) {
      if (mounted.current) setError(err.message || 'Could not connect the downloader.');
    } finally {
      mutating.current = false;
      if (mounted.current) setBusy(false);
    }
  };

  const connected = status?.enabled && Boolean(status.session_id) && status.registered_tools?.length > 0;
  const stateLabel = !status?.enabled ? 'Off' : status.last_error ? 'Needs attention' : connected ? 'Connected' : 'Waiting to connect';
  return <details className="recommendation-settings-card connector-integration" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary><span className="recommendation-card-icon"><Icon name="settings" size={22} /></span><span><strong>The Connector integration</strong><span>Let your assistant search, download, and learn from your viewing.</span></span></summary>
    {expanded && <div className="connector-integration-body">
      <p className="recommendation-help">Enable tools for video search, requested downloads, recommendation hints, and browsing your library, Watch later, and watch history from The Connector.</p>
      <p className="recommendation-help">This also sends searches, watch progress every minute and when playback finishes, recent watched tags, and recommendations shown to you into a Connector system session. It works independently of the AI recommendations switch. Logging itself does not call a model.</p>
      {loading && <p role="status" className="recommendation-help">Loading Connector status...</p>}
      {error && <p className="error-banner" role="alert">{error}</p>}
      {status && <>
        <label className="recommendation-toggle">
          <span className="recommendation-toggle-label">Enable Connector tools and activity log</span>
          <input type="checkbox" role="switch" aria-label="Enable Connector tools and activity log" checked={Boolean(status.enabled)} disabled={busy} onChange={(event) => change(() => updateConnectorSettings({ enabled: event.target.checked }))} />
          <span className="recommendation-switch" aria-hidden="true" />
        </label>
        <div className="connector-integration-status" role="status">
          <strong>{stateLabel}</strong>
          {status.enabled && <span>{status.registered_tools?.length || 0} tools registered · {status.pending_events || 0} activity events waiting</span>}
        </div>
        {status.connector_url && <p className="recommendation-connection">Connector: <code>{status.connector_url}</code></p>}
        {status.session_id && <p className="recommendation-connection">System session: <code>{status.session_id}</code></p>}
        <p className="recommendation-help">To read the activity log in The Connector, turn on Show System Sessions in its sidebar settings.</p>
        {status.last_error && <p className="error-banner" role="alert">{status.last_error}</p>}
        <div className="recommendation-form-actions"><button type="button" className="btn-secondary" disabled={busy || !status.enabled} onClick={() => change(connectDownloader)}><Icon name="refresh" size={15} />{busy ? 'Connecting...' : error || status.last_error ? 'Retry connection' : 'Connect now'}</button></div>
        <details className="connector-integration-advanced">
          <summary>Advanced connection settings</summary>
          <form onSubmit={(event) => { event.preventDefault(); change(() => updateConnectorSettings({ enabled: Boolean(status.enabled), base_url: baseUrl.trim() })); }}>
            <label className="recommendation-field" htmlFor="connector-downloader-url">Downloader address<input id="connector-downloader-url" type="url" required value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} disabled={busy} maxLength={2048} aria-describedby="connector-downloader-url-help" /></label>
            <p id="connector-downloader-url-help" className="recommendation-help">The address The Connector and your browser use to reach this downloader. Keep the default when both apps run on this computer.</p>
            <button className="btn-secondary" disabled={busy || !baseUrl.trim() || baseUrl.trim() === status.base_url}>Save address</button>
          </form>
        </details>
      </>}
    </div>}
  </details>;
}
