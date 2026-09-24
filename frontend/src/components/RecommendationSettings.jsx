import React, { useCallback, useEffect, useRef, useState } from 'react';
import { getRecommendationModels, importRecommendationConfig } from '../services/api';
import { RecommendationToggle, useRecommendations } from './RecommendationContext';
import { recommendationProviderIcon } from '../recommendationUtils';
import FallbackSettings from './FallbackSettings';
import ProviderSearchSettings from './ProviderSearchSettings';
import ConnectorIntegrationSettings from './ConnectorIntegrationSettings';
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
  const [customPrompt, setCustomPrompt] = useState(settings.custom_prompt || '');
  const [providerDomain, setProviderDomain] = useState('');
  const [providerName, setProviderName] = useState('');
  const [providerSearchUrl, setProviderSearchUrl] = useState('');
  const [providerThumbnailDomains, setProviderThumbnailDomains] = useState('');
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
  useEffect(() => { setCustomPrompt(settings.custom_prompt || ''); }, [settings.custom_prompt]);

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
      return true;
    } catch { return false; /* Shared settings error is displayed below. */ }
  };

  const addProvider = async (event) => {
    event.preventDefault();
    setFormError('');
    setNotice('');
    let domain;
    try {
      const value = providerDomain.trim();
      const url = new URL(value.includes('://') ? value : `https://${value}`);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port || !['', '/'].includes(url.pathname) || url.search || url.hash || !url.hostname.includes('.')) throw new Error();
      domain = url.hostname.toLowerCase().replace(/^www\./, '').replace(/\.$/, '');
    } catch {
      setFormError('Enter a website domain, such as bilibili.tv, vimeo.com, or instagram.com.');
      return;
    }
    if (settings.providers.some((provider) => provider.domain === domain)) {
      setFormError('This website is already in your recommendation providers.');
      return;
    }
    const id = domain === 'youtube.com' ? 'youtube' : domain === 'rumble.com' ? 'rumble' : domain;
    const name = providerName.trim() || domain;
    const thumbnailDomains = [...new Set(providerThumbnailDomains.split(/[,\n]/).map((value) => value.trim()).filter(Boolean))];
    if (thumbnailDomains.length > 8) {
      setFormError('Use no more than eight thumbnail CDN domains.');
      return;
    }
    if (await save({ providers: [...settings.providers, { id, name, domain, enabled: true, ...(!['youtube', 'rumble'].includes(id) && providerSearchUrl.trim() ? { search_url: providerSearchUrl.trim() } : {}), ...(thumbnailDomains.length ? { thumbnail_domains: thumbnailDomains } : {}) }] }, `${name} added to recommendation providers.${settings.allow_unverified_links ? '' : ' Enable Show unverified links below if you also want links that the website cannot confirm.'}`)) {
      setProviderDomain('');
      setProviderName('');
      setProviderSearchUrl('');
      setProviderThumbnailDomains('');
    }
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
          <p id="recommendation-seeds-help" className="recommendation-help">Add up to six topics, separated by commas or new lines (100 characters each). As you watch, your history helps the model discover related videos from your enabled websites.</p>
          <button className="btn-primary" disabled={loading || saving}>Save interests</button>
        </form>
      </section>

      <section className="recommendation-settings-card" aria-labelledby="recommendation-custom-prompt-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="bolt" size={22} /></span><div><h2 id="recommendation-custom-prompt-heading">Custom recommendation hint</h2><p>Give your model extra guidance about what you want to watch.</p></div></div>
        <form onSubmit={(event) => {
          event.preventDefault();
          save({ custom_prompt: customPrompt.trim() }, customPrompt.trim() ? 'Custom recommendation hint saved.' : 'Custom recommendation hint cleared.');
        }}>
          <label className="recommendation-field" htmlFor="recommendation-custom-prompt">Hint for your model
            <textarea id="recommendation-custom-prompt" value={customPrompt} onChange={(event) => setCustomPrompt(event.target.value)} rows={5} maxLength={2000} placeholder="For example: Prefer practical tutorials under 20 minutes, avoid spoilers, and include a mix of familiar and new creators." disabled={loading || saving} aria-describedby="recommendation-custom-prompt-help" />
          </label>
          <p id="recommendation-custom-prompt-help" className="recommendation-help">This text is sent to your selected Connector model as a user prompt during topic discovery and final ranking. You can update or clear it at any time. Website and verification restrictions still apply.</p>
          <div className="recommendation-form-actions"><button className="btn-primary" disabled={loading || saving}>Save hint</button><span className="recommendation-help">{customPrompt.length}/2000</span></div>
        </form>
      </section>

      <section className="recommendation-settings-card recommendation-providers" aria-labelledby="recommendation-providers-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="globe" size={22} /></span><div><h2 id="recommendation-providers-heading">Recommendation websites</h2><p>Enabled websites are available in Search videos and AI recommendations. Manual search works with AI off.</p></div></div>
        <ul className="recommendation-provider-list">
          {settings.providers.map((provider) => <li key={provider.id} data-provider={provider.id}>
            <Icon name={recommendationProviderIcon(provider.id)} size={20} />
            <div className="recommendation-provider-details"><strong>{provider.name}</strong><span>{provider.domain}</span></div>
            <label className="recommendation-toggle">
              <input type="checkbox" role="switch" aria-label={`Recommendations from ${provider.name}`} checked={provider.enabled} disabled={loading || saving} onChange={(event) => save({ providers: settings.providers.map((entry) => entry.id === provider.id ? { ...entry, enabled: event.target.checked } : entry) }, `${provider.name} ${event.target.checked ? 'enabled' : 'disabled'} for recommendations.`)} />
              <span className="recommendation-switch" aria-hidden="true" />
            </label>
            {!['youtube', 'rumble'].includes(provider.id) && <button className="icon-btn" aria-label={`Remove ${provider.name}`} disabled={loading || saving} onClick={() => save({ providers: settings.providers.filter((entry) => entry.id !== provider.id) }, `${provider.name} removed from recommendation providers.`)}><Icon name="trash" size={17} /></button>}
            {!['youtube', 'rumble'].includes(provider.id) && <ProviderSearchSettings provider={provider} disabled={loading || saving} onSave={(providerSettings) => save({ providers: settings.providers.map((entry) => entry.id === provider.id ? { ...entry, ...providerSettings } : entry) }, `${provider.name} website settings saved.`)} />}
          </li>)}
        </ul>
        {!settings.providers.some((provider) => provider.enabled) && <p className="recommendation-help" role="status">Enable at least one website to receive recommendations.</p>}
        <form onSubmit={addProvider}>
          <label className="recommendation-field" htmlFor="recommendation-provider-domain">Website domain<input id="recommendation-provider-domain" value={providerDomain} onChange={(event) => setProviderDomain(event.target.value)} placeholder="bilibili.tv, vimeo.com, or instagram.com" maxLength={253} disabled={loading || saving} aria-describedby="recommendation-provider-help" required /></label>
          <label className="recommendation-field" htmlFor="recommendation-provider-name">Website name (optional)<input id="recommendation-provider-name" value={providerName} onChange={(event) => setProviderName(event.target.value)} maxLength={60} placeholder="My favorite video website" disabled={loading || saving} /></label>
          <label className="recommendation-field" htmlFor="recommendation-provider-search-url">Search URL (optional)<input id="recommendation-provider-search-url" value={providerSearchUrl} onChange={(event) => setProviderSearchUrl(event.target.value)} placeholder="https://vimeo.com/search?q={query}" maxLength={2048} disabled={loading || saving} /></label>
          <p className="recommendation-help">For a custom website, use its HTTPS search URL with {'{query}'}, or a prefix ending in a query parameter such as ?q=. The URL must belong to that website.</p>
          <label className="recommendation-field" htmlFor="recommendation-provider-thumbnail-domains">Thumbnail CDN domains (optional)<input id="recommendation-provider-thumbnail-domains" value={providerThumbnailDomains} onChange={(event) => setProviderThumbnailDomains(event.target.value)} placeholder="images.example-cdn.com, img.example.net" maxLength={2048} disabled={loading || saving} /></label>
          <p className="recommendation-help">Add up to eight public CDN domain names, separated by commas. This allows thumbnails hosted away from the video website. Do not include a protocol or path.</p>
          <p id="recommendation-provider-help" className="recommendation-help">Add up to 12 public websites to include in discovery. Supported websites use their own video search; other websites require a configured search URL. Enable Show unverified links below to include model-suggested links that the website cannot confirm. Downloads depend on website support; recommendations also link to the original video.</p>
          {settings.providers.length >= 12 && <p className="recommendation-help" role="status">You have 12 websites. Remove a custom website to add another.</p>}
          <button className="btn-primary" disabled={loading || saving || !providerDomain.trim() || settings.providers.length >= 12}><Icon name="plus" size={16} />Add website</button>
        </form>
      </section>

      <section className="recommendation-settings-card" aria-labelledby="recommendation-links-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="link" size={22} /></span><div><h2 id="recommendation-links-heading">Unverified recommendation links</h2><p>Include discoveries whose video metadata could not be confirmed.</p></div></div>
        <p className="recommendation-help">Enable this to show links suggested by your model when their video metadata cannot be verified. They appear with an Unverified label and may be unavailable. Verified results from website video search appear with this switch off.</p>
        <label className="recommendation-toggle">
          <span className="recommendation-toggle-label">Show unverified links</span>
          <input type="checkbox" role="switch" checked={Boolean(settings.allow_unverified_links)} disabled={loading || saving} onChange={(event) => save({ allow_unverified_links: event.target.checked }, event.target.checked ? 'Unverified recommendation links enabled.' : 'Unverified recommendation links hidden.')} />
          <span className="recommendation-switch" aria-hidden="true" />
        </label>
      </section>

      <section className="recommendation-settings-card" aria-labelledby="recommendation-fetch-links-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="search" size={22} /></span><div><h2 id="recommendation-fetch-links-heading">Search-page link collection</h2><p>Choose a bounded selection or every usable link on each configured search page.</p></div></div>
        <p className="recommendation-help">Off uses the normal selected search results. On collects up to 200 usable same-website video links from the returned page before the recommendation system filters and ranks them.</p>
        <label className="recommendation-toggle">
          <span className="recommendation-toggle-label">Fetch all links</span>
          <input type="checkbox" role="switch" aria-label="Fetch all search-page links for recommendations" checked={Boolean(settings.fetch_all_search_links)} disabled={loading || saving} onChange={(event) => save({ fetch_all_search_links: event.target.checked }, event.target.checked ? 'Recommendations will fetch all usable search-page links.' : 'Recommendations will use selected search-page links.')} />
          <span className="recommendation-switch" aria-hidden="true" />
        </label>
      </section>

      <section className="recommendation-settings-card" aria-labelledby="recommendation-ai-titles-heading">
        <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="bolt" size={22} /></span><div><h2 id="recommendation-ai-titles-heading">AI title fallback</h2><p>Ask your selected Connector model only when normal title lookup fails.</p></div></div>
        <p className="recommendation-help">The final video URL is sent to the selected model. AI-generated titles stay marked as unverified and are labeled in Watch later. Direct website metadata remains the first choice.</p>
        <label className="recommendation-toggle">
          <span className="recommendation-toggle-label">Allow AI to fetch missing titles</span>
          <input type="checkbox" role="switch" checked={Boolean(settings.allow_ai_title_lookup)} disabled={loading || saving || !settings.model_id} onChange={(event) => save({ allow_ai_title_lookup: event.target.checked }, event.target.checked ? 'AI title fallback enabled.' : 'AI title fallback disabled.')} />
          <span className="recommendation-switch" aria-hidden="true" />
        </label>
        {!settings.model_id && <p className="recommendation-help" role="status">Choose a Connector model above to enable AI title fallback.</p>}
      </section>

      <FallbackSettings />

      <ConnectorIntegrationSettings />

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
