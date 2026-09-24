import React, { useEffect, useMemo, useState } from 'react';
import { getWatchHistory, startDownloads } from '../services/api';
import { connectorBadgeClass, formatDuration } from '../mediaUtils';
import { recommendationProviderIcon, recommendationProviderLabel, recommendationSource } from '../recommendationUtils';
import { useRecommendations } from './RecommendationContext';
import Icon from './Icon';
import RecommendationPanel from './RecommendationPanel';
import './Recommendations.css';

export default function WatchHistoryPage({ navigate }) {
  const { settings } = useRecommendations();
  const providers = settings.providers;
  const [history, setHistory] = useState(null);
  const [error, setError] = useState('');
  const [source, setSource] = useState('all');
  const [redownloads, setRedownloads] = useState({});
  useEffect(() => {
    const controller = new AbortController();
    getWatchHistory(controller.signal).then((items) => {
      if (controller.signal.aborted) return;
      setHistory(items);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(err.message || 'Could not load watch history.');
    });
    return () => controller.abort();
  }, []);
  const sourcedHistory = useMemo(() => (history || []).map((item) => ({ ...item, providerId: recommendationSource(item, providers) })), [history, providers]);
  const sourceIds = [...new Set(['all', ...providers.map((provider) => provider.id), ...sourcedHistory.map((item) => item.providerId)])];
  const sourceLabel = (id) => recommendationProviderLabel(id, providers);
  const downloadAgain = async (item) => {
    const key = item.source_url;
    if (redownloads[key]?.status === 'starting') return;
    setRedownloads((previous) => ({ ...previous, [key]: { status: 'starting' } }));
    try {
      const media = (await startDownloads([item.source_url], 'best'))?.[0];
      if (!media?.id) throw new Error('The video could not be added to Downloads.');
      if (media.status === 'ready') {
        setHistory((previous) => previous.map((entry) => entry.video_id === item.video_id ? { ...entry, media_id: media.id, thumbnail_url: media.thumbnail_url || null } : entry));
        setRedownloads((previous) => ({ ...previous, [key]: { status: 'ready' } }));
      } else {
        setRedownloads((previous) => ({ ...previous, [key]: { status: 'queued', media_id: media.id } }));
      }
    } catch (err) {
      setRedownloads((previous) => ({ ...previous, [key]: { status: 'failed', error: err.message || 'Could not download this video again.' } }));
    }
  };
  const filtered = useMemo(() => sourcedHistory.filter((item) => source === 'all' || item.providerId === source), [sourcedHistory, source]);
  return <div className="page-wrap watch-history-page">
    <div className="page-heading"><div><p className="eyebrow">PICK UP WHERE YOU LEFT OFF</p><h1>Watch history<span className="accent">.</span></h1><p className="page-description">Review what you watched and discover related videos across your enabled websites.</p></div></div>
    <div className="source-filters watch-history-sources" aria-label="Watched video websites">
      {sourceIds.map((id) => <button key={id} className={source === id ? 'selected' : ''} aria-pressed={source === id} onClick={() => setSource(id)}><Icon name={recommendationProviderIcon(id)} size={15} />{id === 'all' ? 'All websites' : sourceLabel(id)}</button>)}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {history === null && !error ? <p className="muted" role="status">Loading watch history...</p>
      : <div className="watch-history-layout"><section className="watch-history-list" aria-label="Watched videos">
        <div className="section-title"><h2>{source === 'all' ? 'Recently watched' : `Watched on ${sourceLabel(source)}`}<span className="count-pill">{filtered.length}</span></h2></div>
        {filtered.length ? filtered.map((item) => <article className="watch-history-item" key={item.video_id}>
          <span className={`watch-history-thumbnail source-${item.providerId}`}><Icon name={recommendationProviderIcon(item.providerId)} size={24} />{item.thumbnail_url && <img src={item.thumbnail_url} alt="" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; }} />}</span>
          <div><span className={connectorBadgeClass(item.providerId)}>{sourceLabel(item.providerId)}</span><h3>{item.title || item.source_url}</h3><p>{[item.uploader, formatDuration(item.position_seconds), new Date(item.last_watched_at).toLocaleString()].filter(Boolean).join(' · ')}</p>{redownloads[item.source_url]?.error && <p className="watch-history-download-error" role="alert">{redownloads[item.source_url].error}</p>}</div>
          {item.media_id ? <button className="btn-secondary" onClick={() => navigate(`watch/${item.media_id}`)}>Open</button>
            : redownloads[item.source_url]?.status === 'queued' ? <button className="btn-secondary" onClick={() => navigate('downloads')}>View download</button>
              : <button className="btn-secondary" disabled={redownloads[item.source_url]?.status === 'starting'} onClick={() => downloadAgain(item)}><Icon name="download" size={14} />{redownloads[item.source_url]?.status === 'starting' ? 'Adding...' : redownloads[item.source_url]?.status === 'failed' ? 'Retry download' : 'Download again'}</button>}
        </article>) : <div className="collection-empty"><Icon name="history" size={29} /><h3>No watch history for this website</h3><p>Play a downloaded video to add it here.</p></div>}
      </section><RecommendationPanel context="history" navigate={navigate} /></div>}
  </div>;
}
