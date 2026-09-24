import React, { useEffect, useState } from 'react';
import { detectRecommendationThumbnailDomains } from '../services/api';

export default function ProviderSearchSettings({ provider, disabled, onSave }) {
  const [url, setUrl] = useState(provider.search_url || '');
  const [thumbnailDomains, setThumbnailDomains] = useState((provider.thumbnail_domains || []).join(', '));
  const [detecting, setDetecting] = useState(false);
  const [detectionMessage, setDetectionMessage] = useState('');
  useEffect(() => { setUrl(provider.search_url || ''); }, [provider.search_url]);
  useEffect(() => { setThumbnailDomains((provider.thumbnail_domains || []).join(', ')); }, [provider.thumbnail_domains]);
  return <details className="provider-search-settings"><summary>Search and thumbnail settings</summary><form onSubmit={(event) => {
    event.preventDefault();
    onSave({
      search_url: url.trim() || null,
      thumbnail_domains: [...new Set(thumbnailDomains.split(/[,\n]/).map((domain) => domain.trim()).filter(Boolean))],
    });
  }}>
    <label className="recommendation-field">Search URL for {provider.name}<input aria-label={`Search URL for ${provider.name}`} value={url} onChange={(event) => setUrl(event.target.value)} placeholder={`https://${provider.domain}/search?q={query}`} maxLength={2048} disabled={disabled} /></label>
    <p className="recommendation-help">Use this website's search URL with {'{query}'}, or a prefix ending in its query parameter, such as ?q=. Leave blank to use the default search.</p>
    <label className="recommendation-field">Thumbnail CDN domains<input aria-label={`Thumbnail CDN domains for ${provider.name}`} value={thumbnailDomains} onChange={(event) => setThumbnailDomains(event.target.value)} placeholder="images.example-cdn.com, img.example.net" maxLength={2048} disabled={disabled} /></label>
    <p className="recommendation-help">Optional. Enter up to eight public domain names, separated by commas. Images from these domains and their subdomains will be allowed. Do not include <code>https://</code> or a path.</p>
    {detectionMessage && <p className="recommendation-help" role="status">{detectionMessage}</p>}
    <div className="recommendation-form-actions"><button className="btn-secondary" disabled={disabled}>Save website settings</button><button type="button" className="btn-secondary" disabled={disabled || detecting} onClick={async () => {
      setDetecting(true);
      setDetectionMessage('');
      try {
        const result = await detectRecommendationThumbnailDomains(provider.id);
        const domains = result.domains || [];
        if (domains.length) {
          setThumbnailDomains((current) => [...new Set([...current.split(/[,\n]/).map((domain) => domain.trim()).filter(Boolean), ...domains])].join(', '));
          setDetectionMessage('Found candidate image domains. Review them, then save website settings.');
        } else setDetectionMessage('No external thumbnail domains were found on the page.');
      } catch (error) {
        setDetectionMessage(error.message || 'Thumbnail domains could not be detected.');
      } finally { setDetecting(false); }
    }}>{detecting ? 'Detecting…' : 'Detect from website'}</button></div>
  </form></details>;
}
