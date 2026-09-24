import React, { useEffect, useState } from 'react';
import { useRecommendations } from './RecommendationContext';
import { recommendationProviderLabel } from '../recommendationUtils';
import Icon from './Icon';
import './VideoSearch.css';

export function videoSearchRoute(query, source = 'all', fetchAll = false) {
  const params = new URLSearchParams({ q: query.trim(), source });
  if (fetchAll) params.set('fetch_all', 'true');
  return `search?${params}`;
}

export default function VideoSearchForm({ query = '', source = 'all', fetchAll = false, onSearch }) {
  const { settings, settingsLoaded, loading, error, reloadSettings, openSettings } = useRecommendations();
  const [text, setText] = useState(query);
  const [platform, setPlatform] = useState(source);
  const [allLinks, setAllLinks] = useState(fetchAll);
  const providers = settings.providers.filter((provider) => provider.enabled);
  const available = providers.some((provider) => platform === 'all' || provider.id === platform);
  const ready = settingsLoaded && !loading;
  const sourceName = recommendationProviderLabel(platform, settings.providers);

  useEffect(() => { setText(query); setPlatform(source); setAllLinks(fetchAll); }, [query, source, fetchAll]);

  const submit = (event) => {
    event.preventDefault();
    if (text.trim() && ready && available) onSearch(text.trim(), platform, allLinks);
  };

  return <div className="video-search-controls"><form className="video-search-form" aria-label="Search online videos" role="search" onSubmit={submit}>
    <div className="video-search-input">
      <Icon name="search" size={21} />
      <input type="search" aria-label="Search online videos" placeholder="Search videos, topics, or creators…" maxLength={200} value={text} onChange={(event) => setText(event.target.value)} />
    </div>
    <select aria-label="Search website" value={platform} onChange={(event) => setPlatform(event.target.value)} disabled={!ready}>
      <option value="all">All enabled websites</option>
      {platform !== 'all' && !available && <option value={platform} disabled>{sourceName} ({loading ? 'loading…' : 'not enabled'})</option>}
      {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
    </select>
    <button className="btn-primary" type="submit" disabled={!text.trim() || !ready || !available}><Icon name="search" size={16} />Search</button>
    <label className="video-search-fetch-all"><input type="checkbox" role="switch" aria-label="Fetch all links" checked={allLinks} onChange={(event) => setAllLinks(event.target.checked)} disabled={!ready} /><span>Fetch all links</span></label>
  </form>
    {loading && <p className="video-search-availability" role="status">Loading search websites…</p>}
    {!loading && !settingsLoaded && error && <div className="video-search-availability" role="alert"><span>Website settings unavailable. {error}</span><button className="link-btn" onClick={reloadSettings}>Reload websites</button></div>}
    {ready && !available && <div className="video-search-availability" role="status"><span>{providers.length === 0 ? 'Enable at least one website to search for videos.' : `${sourceName} is not enabled. Choose an enabled website or update your website settings.`}</span><button className="link-btn" onClick={openSettings}>Website settings</button></div>}
  </div>;
}
