import React, { useCallback, useEffect, useRef, useState } from 'react';
import { getRecommendationModels, importRecommendationConfig } from '../services/api';
import { RecommendationToggle, useRecommendations } from './RecommendationContext';
import Icon from './Icon';
import './Recommendations.css';

const MAX_CONFIG_BYTES = 128 * 1024;

export default function RecommendationSettings() {
  const { settings, loading, saving, error, updateSettings, reloadSettings } = useRecommendations();
  const [models, setModels] = useState([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState('');
  const [connectorUrl, setConnectorUrl] = useState('');
  const [selectedModel, setSelectedModel] = useState(settings.model_id || '');
  const [seedText, setSeedText] = useState((settings.seed_keywords || []).join(', '));
  const [configName, setConfigName] = useState('');
  const [file, setFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [formError, setFormError] = useState('');
  const [notice, setNotice] = useState('');
  const controllerRef = useRef(null);
  const mounted = useRef(false);
  const fileRef = useRef(null);

  useEffect(() => { setSelectedModel(settings.model_id || ''); }, [settings.model_id]);
  const seedKey = JSON.stringify(settings.seed_keywords || []);
  useEffect(() => { setSeedText(JSON.parse(seedKey).join(', ')); }, [seedKey]);

  const refreshModels = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setModelsLoading(true);
    setModelsError('');
    try {
      const result = await getRecommendationModels(controller.signal);
      if (!controller.signal.aborted) {
        setModels(result.models || []);
        setConnectorUrl(result.connector_url || '');
      }
    } catch (err) {
      if (!controller.signal.aborted) setModelsError(err.message || 'Could not connect to The Connector.');
    } finally {
      if (!controller.signal.aborted) setModelsLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refreshModels();
    return () => { mounted.current = false; controllerRef.current?.abort(); };
  }, [refreshModels]);

  const save = async (patch, message) => {
    setNotice('');
    setFormError('');
    try {
      await updateSettings(patch);
      if (mounted.current) setNotice(message);
    } catch { /* Shared settings error is displayed below. */ }
  };

  const importConfig = async (event) => {
    event.preventDefault();
    setNotice('');
    setFormError('');
    if (!file) { setFormError('Choose a config.json file first.'); return; }
    if (file.size > MAX_CONFIG_BYTES) { setFormError('Choose a JSON configuration no larger than 128 KiB.'); return; }
    setImporting(true);
    try {
      let config;
      try { config = JSON.parse((await file.text()).replace(/^\uFEFF/, '')); }
      catch { throw new Error('This file is not valid JSON. Choose a Connector config.json file.'); }
      if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('The configuration must be a JSON object.');
      const imported = await importRecommendationConfig(configName.trim() || file.name.replace(/\.json$/i, '').slice(0, 100), config);
      if (!mounted.current) return;
      await updateSettings({ model_id: imported.model_id });
      if (!mounted.current) return;
      await refreshModels();
      if (!mounted.current) return;
      setSelectedModel(imported.model_id);
      setFile(null);
      setConfigName('');
      if (fileRef.current) fileRef.current.value = '';
      setNotice(`Imported and selected ${imported.name || imported.model_id}. Use the switch above to enable recommendations.`);
    } catch (err) {
      if (mounted.current) setFormError(err.message || 'Could not import this configuration.');
    } finally {
      if (mounted.current) setImporting(false);
    }
  };

  const selectedDescription = models.find((model) => model.id === selectedModel)?.description;
  const selectedMissing = selectedModel && !models.some((model) => model.id === selectedModel);
  return <div className="page-wrap recommendation-settings">
    <div className="page-heading"><div>
      <p className="eyebrow">FIND YOUR NEXT FAVORITE</p>
      <h1>AI recommendations<span className="accent">.</span></h1>
      <p className="page-description">Choose your own model configuration and shape what plays next.</p>
    </div><RecommendationToggle /></div>

    <div className="recommendation-privacy"><Icon name="history" size={21} /><p>When enabled, watched video titles and video metadata are sent to your selected model through The Connector to find and rank videos. Watch history is stored in this app's local database. Turn recommendations off anytime using the switch in the header.</p></div>
    {loading && <p role="status" className="muted">Loading recommendation settings...</p>}
    {error && <div className="error-banner" role="alert"><span>{error}</span><button className="link-btn" disabled={saving} onClick={reloadSettings}>Reload settings</button></div>}
    {formError && <div className="error-banner" role="alert">{formError}</div>}
    {notice && <div className="success-banner" role="status"><Icon name="check" size={17} /><span>{notice}</span></div>}

    <div className="recommendation-settings-grid">
      <section className="recommendation-settings-card" aria-labelledby="recommendation-model-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="settings" size={22} /></span><div><h2 id="recommendation-model-heading">Your recommendation model</h2><p>Active configurations from The Connector.</p></div></div>
        {(connectorUrl || settings.connector_url) && <p className="recommendation-connection">Connected through <code>{connectorUrl || settings.connector_url}</code></p>}
        {modelsError && <div className="error-banner" role="alert"><span>{modelsError}</span><button className="link-btn" onClick={refreshModels}>Retry connection</button></div>}
        {!modelsLoading && !modelsError && !models.length && <p className="recommendation-help" role="status">No active configurations found. Upload your config.json below, or activate a saved configuration in The Connector and refresh this list.</p>}
        <form onSubmit={(event) => { event.preventDefault(); if (selectedModel) save({ model_id: selectedModel }, 'Recommendation model saved.'); }}>
          <label className="recommendation-field" htmlFor="recommendation-model">Model configuration
            <select id="recommendation-model" value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} disabled={modelsLoading || loading || saving || importing}>
              <option value="">{modelsLoading ? 'Loading configurations...' : 'Choose a configuration'}</option>
              {selectedMissing && <option value={selectedModel} disabled>{selectedModel} (unavailable)</option>}
              {models.map((model) => <option key={model.id} value={model.id}>{model.name || model.id}{model.name && model.name !== model.id ? ` (${model.id})` : ''}</option>)}
            </select>
          </label>
          {selectedDescription && <p className="recommendation-help">{selectedDescription}</p>}
          <div className="recommendation-form-actions"><button className="btn-primary" disabled={!selectedModel || selectedMissing || modelsLoading || loading || saving || importing}>Save model</button><button type="button" className="btn-secondary" onClick={refreshModels} disabled={modelsLoading}><Icon name="refresh" size={15} />{modelsLoading ? 'Refreshing...' : 'Refresh models'}</button></div>
        </form>
      </section>

      <section className="recommendation-settings-card" aria-labelledby="recommendation-interests-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="search" size={22} /></span><div><h2 id="recommendation-interests-heading">Start with your interests</h2><p>Give a new or empty feed somewhere to begin.</p></div></div>
        <form onSubmit={(event) => {
          event.preventDefault();
          const keywords = [...new Set(seedText.split(/[,\n]/).map((word) => word.trim()).filter(Boolean))];
          if (keywords.length > 6 || keywords.some((word) => word.length > 100)) {
            setFormError('Use up to six topics, with no more than 100 characters per topic.');
            setNotice('');
            return;
          }
          save({ seed_keywords: keywords }, 'Interests saved.');
        }}>
          <label className="recommendation-field" htmlFor="recommendation-seeds">Topics or keywords
            <textarea id="recommendation-seeds" value={seedText} onChange={(event) => setSeedText(event.target.value)} rows={3} placeholder="Space exploration, cooking, live music" disabled={loading || saving} aria-describedby="recommendation-seeds-help" />
          </label>
          <p id="recommendation-seeds-help" className="recommendation-help">Add up to six topics, separated by commas or new lines (100 characters each). As you watch, your history helps the model discover related videos from YouTube and Rumble.</p>
          <button className="btn-primary" disabled={loading || saving}>Save interests</button>
        </form>
      </section>

      <section className="recommendation-settings-card recommendation-import" aria-labelledby="recommendation-import-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="folder" size={22} /></span><div><h2 id="recommendation-import-heading">Bring your own config.json</h2><p>Import a native Connector routing configuration and select it for recommendations.</p></div></div>
        <form onSubmit={importConfig}>
          <div className="recommendation-import-fields"><label className="recommendation-field" htmlFor="recommendation-config-name">Configuration name (optional)<input id="recommendation-config-name" value={configName} onChange={(event) => setConfigName(event.target.value)} maxLength={100} placeholder="My recommendation model" disabled={importing} /></label>
            <label className="recommendation-field" htmlFor="recommendation-config-file">Configuration file<input ref={fileRef} id="recommendation-config-file" type="file" accept=".json,application/json" onChange={(event) => { setFile(event.target.files?.[0] || null); setFormError(''); }} disabled={importing} aria-describedby="recommendation-config-help" /></label></div>
          <p id="recommendation-config-help" className="recommendation-help">Choose the config.json routing object exported by The Connector (up to 128 KiB). Provider credentials are managed in The Connector. Importing saves an active configuration there.</p>
          <button className="btn-primary" disabled={!file || importing || loading || saving}><Icon name="plus" size={16} />{importing ? 'Importing configuration...' : 'Import & select'}</button>
        </form>
      </section>
    </div>
  </div>;
}
