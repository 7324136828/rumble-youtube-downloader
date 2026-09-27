import React, { useEffect, useState } from 'react';
import { detectRecommendationThumbnailDomains } from '../services/api';
import Icon from './Icon';

const parseDomains = (text) => [...new Set(text.split(/[,\n]/).map((domain) => domain.trim()).filter(Boolean))];

export default function ThumbnailDomainSettings({ domains, providers, disabled, onSave }) {
  const domainKey = JSON.stringify(domains || []);
  const [text, setText] = useState((domains || []).join('\n'));
  const [websiteId, setWebsiteId] = useState('');
  const [detecting, setDetecting] = useState(false);
  const [message, setMessage] = useState('');
  const websites = providers.filter((provider) => !['youtube', 'rumble'].includes(provider.id));
  const selectedId = websites.some((provider) => provider.id === websiteId) ? websiteId : (websites[0]?.id || '');
  useEffect(() => { setText(JSON.parse(domainKey).join('\n')); }, [domainKey]);

  return <section className="recommendation-settings-card" aria-labelledby="thumbnail-domains-heading">
    <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="globe" size={22} /></span><div><h2 id="thumbnail-domains-heading">Thumbnail CDN domains</h2><p>A shared list for all video websites.</p></div></div>
    <form onSubmit={(event) => {
      event.preventDefault();
      const next = parseDomains(text);
      if (next.length > 128) { setMessage('Use no more than 128 thumbnail CDN domains.'); return; }
      setMessage('');
      onSave(next);
    }}>
      <label className="recommendation-field" htmlFor="thumbnail-domains">Allowed CDN domains
        <textarea id="thumbnail-domains" value={text} onChange={(event) => setText(event.target.value)} rows={5} maxLength={32768} disabled={disabled || detecting} placeholder="images.example-cdn.com" aria-describedby="thumbnail-domains-help" />
      </label>
      <p id="thumbnail-domains-help" className="recommendation-help">Enter up to 128 public domain names, separated by commas or new lines. These domains and their subdomains can serve thumbnails for any website. This list is saved independently of your recommendation websites.</p>
      {message && <p className="recommendation-help" role="status">{message}</p>}
      <button className="btn-primary" disabled={disabled || detecting}>Save CDN domains</button>
      {websites.length > 0 && <>
        <label className="recommendation-field" htmlFor="thumbnail-detection-website">Website to inspect
          <select id="thumbnail-detection-website" value={selectedId} onChange={(event) => setWebsiteId(event.target.value)} disabled={disabled || detecting}>
            {websites.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
          </select>
        </label>
        <button type="button" className="btn-secondary" disabled={disabled || detecting} onClick={async () => {
          setDetecting(true);
          setMessage('');
          try {
            const result = await detectRecommendationThumbnailDomains(selectedId);
            const candidates = result.domains || [];
            if (candidates.length) {
              setText((current) => [...new Set([...parseDomains(current), ...candidates])].join('\n'));
              setMessage('Found candidate image domains. Review them, then save CDN domains.');
            } else setMessage('No external thumbnail domains were found on the page.');
          } catch (error) {
            setMessage(error.message || 'Thumbnail domains could not be detected.');
          } finally { setDetecting(false); }
        }}>{detecting ? 'Detecting…' : 'Detect from website'}</button>
      </>}
    </form>
  </section>;
}
