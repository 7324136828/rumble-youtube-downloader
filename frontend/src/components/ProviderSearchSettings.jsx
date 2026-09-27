import React, { useEffect, useState } from 'react';

export default function ProviderSearchSettings({ provider, disabled, onSave }) {
  const [url, setUrl] = useState(provider.search_url || '');
  useEffect(() => { setUrl(provider.search_url || ''); }, [provider.search_url]);
  return <details className="provider-search-settings"><summary>Search settings</summary><form onSubmit={(event) => {
    event.preventDefault();
    onSave({
      search_url: url.trim() || null,
    });
  }}>
    <label className="recommendation-field">Search URL for {provider.name}<input aria-label={`Search URL for ${provider.name}`} value={url} onChange={(event) => setUrl(event.target.value)} placeholder={`https://${provider.domain}/search?q={query}`} maxLength={2048} disabled={disabled} /></label>
    <p className="recommendation-help">Use this website's search URL with {'{query}'}, or a prefix ending in its query parameter, such as ?q=. Leave blank to use the default search.</p>
    <button className="btn-secondary" disabled={disabled}>Save website settings</button>
  </form></details>;
}
