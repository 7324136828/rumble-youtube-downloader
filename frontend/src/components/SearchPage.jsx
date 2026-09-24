import React, { useEffect, useRef, useState } from 'react';
import { saveWatchLater, searchVideos, startDownloads, updateLinkState } from '../services/api';
import { connectorBadgeClass, formatDuration } from '../mediaUtils';
import { recommendationProviderIcon, recommendationProviderLabel } from '../recommendationUtils';
import { useRecommendations } from './RecommendationContext';
import WatchLaterButton from './WatchLaterButton';
import VideoOrigins from './VideoOrigins';
import Icon from './Icon';
import VideoSearchForm, { videoSearchRoute } from './VideoSearchForm';

function newSearchSession() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function SearchResult({ video, providerName, download, onAdd, linkReview, onMarkRepeated }) {
  const [imageFailed, setImageFailed] = useState(false);
  const adding = download?.status === 'adding';
  const added = download?.status === 'added';
  const description = typeof video.description === 'string' ? video.description.trim() : '';
  return <article className="media-card search-result">
    <a className="media-thumb" href={video.source_url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${video.title} on ${providerName} (new tab)`}>
      {video.thumbnail_url && !imageFailed ? <img src={video.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} /> : <span className={`media-thumb-fallback source-${video.connector}`}><Icon name={recommendationProviderIcon(video.connector)} size={42} /></span>}
      <span className={`${connectorBadgeClass(video.connector)} thumb-source`}>{providerName}</span>
      <span className="thumb-play"><Icon name="external" size={22} /></span>
      {formatDuration(video.duration) && <span className="thumb-duration">{formatDuration(video.duration)}</span>}
    </a>
    <div className="media-card-body">
      {video.verified === false && <span className="search-unverified">Unverified link</span>}
      <VideoOrigins video={video} />
      <h3 title={video.title}>{video.title}</h3>
      <p>{video.uploader || providerName}</p>
      {description && <p className="search-result-description" title={description}>{description}</p>}
      <div className="search-result-actions">
        <button className={added ? 'btn-secondary search-added' : 'btn-primary'} disabled={adding || added} onClick={() => onAdd(video)}>
          <Icon name={added ? 'check' : 'plus'} size={15} />{added ? 'Added to queue' : adding ? 'Adding…' : 'Add to library'}
        </button>
        <a className="card-open" href={video.source_url} target="_blank" rel="noopener noreferrer">Open original<Icon name="external" size={14} /><span className="sr-only"> (new tab)</span></a>
        <WatchLaterButton video={video} />
        {video.link_id && <button type="button" className="btn-secondary" disabled={linkReview?.status === 'saving' || linkReview?.status === 'saved'} onClick={() => onMarkRepeated(video)}><Icon name={linkReview?.status === 'saved' ? 'check' : 'history'} size={14} />{linkReview?.status === 'saved' ? 'Marked repeated' : linkReview?.status === 'saving' ? 'Savingâ€¦' : 'Mark repeated'}</button>}
      </div>
      {download?.error && <p className="search-download-error" role="alert">{download.error}</p>}
      {linkReview?.error && <p className="search-download-error" role="alert">{linkReview.error}</p>}
    </div>
  </article>;
}

export default function SearchPage({ query = '', source = 'all', fetchAll = new URLSearchParams(globalThis.location?.hash?.split('?')[1] || '').get('fetch_all') === 'true', navigate }) {
  const { settings, settingsLoaded, noteWatchLaterSaved, reloadSettings } = useRecommendations();
  const [result, setResult] = useState(null);
  const [requestError, setRequestError] = useState(null);
  const [retry, setRetry] = useState(0);
  const [quality, setQuality] = useState('best');
  const [downloads, setDownloads] = useState({});
  const [notice, setNotice] = useState('');
  const [savingAll, setSavingAll] = useState(false);
  const [saveAllError, setSaveAllError] = useState('');
  const [linkReviews, setLinkReviews] = useState({});
  const [page, setPage] = useState({ key: '', limit: 12 });
  const queued = useRef(new Set());
  const liveKey = useRef('');
  const session = useRef({ key: '', id: '' });
  const providers = settings.providers.filter((provider) => provider.enabled);
  const sourceAvailable = providers.some((provider) => source === 'all' || provider.id === source);
  const providersReady = settingsLoaded;
  const canSearch = Boolean(query) && providersReady && sourceAvailable;
  const providerKey = JSON.stringify(settings.providers.map(({ id, domain, enabled, search_url }) => [id, domain, enabled, search_url]));
  const baseKey = JSON.stringify([query, source, fetchAll, providerKey, retry, Boolean(providersReady)]);
  if (session.current.key !== baseKey) session.current = { key: baseKey, id: newSearchSession() };
  const limit = page.key === baseKey ? page.limit : 12;
  const key = JSON.stringify([baseKey, limit, session.current.id]);
  liveKey.current = key;
  const response = result?.key === key ? result.data : null;
  const error = requestError?.key === key ? requestError.message : '';
  const loading = canSearch && !response && !error;
  const sourceName = (id) => recommendationProviderLabel(id, settings.providers);

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setRequestError(null);
    setSaveAllError('');
    if (canSearch) {
      searchVideos(query, source, controller.signal, limit, session.current.id, fetchAll).then((result) => {
        if (!controller.signal.aborted && liveKey.current === key) setResult({ key, data: result });
      }).catch((err) => {
        if (!controller.signal.aborted && liveKey.current === key) setRequestError({ key, message: err.message || 'Video search is temporarily unavailable.' });
      });
    }
    return () => controller.abort();
  }, [query, source, fetchAll, key, canSearch, limit]);

  const search = (nextQuery, nextSource, nextFetchAll) => {
    if (nextQuery === query && nextSource === source && nextFetchAll === fetchAll) setRetry((value) => value + 1);
    else navigate(videoSearchRoute(nextQuery, nextSource, nextFetchAll));
  };

  const addVideo = async (video) => {
    const url = video.source_url;
    if (queued.current.has(url)) return;
    queued.current.add(url);
    setDownloads((previous) => ({ ...previous, [url]: { status: 'adding' } }));
    setNotice('');
    try {
      const started = await startDownloads([url], quality);
      if (!started.length) throw new Error('The video was not added. Please try again.');
      setDownloads((previous) => ({ ...previous, [url]: { status: 'added' } }));
      setNotice(`“${video.title}” was added to your download queue.`);
    } catch (err) {
      queued.current.delete(url);
      setDownloads((previous) => ({ ...previous, [url]: { status: 'error', error: err.message || 'Could not add this video. Try again.' } }));
    }
  };

  const markRepeated = async (video) => {
    if (!video.link_id || linkReviews[video.source_url]?.status === 'saving') return;
    setLinkReviews((previous) => ({ ...previous, [video.source_url]: { status: 'saving' } }));
    try {
      await updateLinkState(video.link_id, 'silenced');
      setLinkReviews((previous) => ({ ...previous, [video.source_url]: { status: 'saved' } }));
      setNotice(`â€œ${video.title}â€ was marked as a repeated link.`);
    } catch (err) {
      setLinkReviews((previous) => ({ ...previous, [video.source_url]: { status: 'error', error: err.message || 'Could not mark this link as repeated.' } }));
    }
  };

  const saveAllForLater = async () => {
    if (savingAll || !response?.results?.length) return;
    setSavingAll(true); setNotice(''); setSaveAllError('');
    try {
      const videos = response.results.map((video) => ({ source_url: video.source_url, ...(video.title ? { title: video.title } : {}), ...(video.description ? { description: video.description } : {}) }));
      const saved = await saveWatchLater(videos);
      noteWatchLaterSaved(videos.map((video) => video.source_url));
      reloadSettings();
      setNotice(`${saved.added || 0} link${saved.added === 1 ? '' : 's'} added to Watch later${saved.updated ? `, ${saved.updated} updated` : ''}.`);
    } catch (err) { setSaveAllError(err.message || 'Could not save these links for later.'); }
    finally { setSavingAll(false); }
  };

  return <div className="page-wrap search-page">
    <div className="page-heading"><div>
      <p className="eyebrow">FIND SOMETHING WORTH WATCHING</p>
      <h1>Search videos<span className="accent">.</span></h1>
      <p className="page-description">Search your enabled websites and save your favorites to watch here.</p>
    </div></div>
    <VideoSearchForm query={query} source={source} fetchAll={fetchAll} onSearch={search} />
    <p className="video-search-hint">Search by keyword on all enabled websites or choose one. Fetch all links returns every usable video link found on a configured search page, up to 200. Manual search works with AI recommendations off.</p>
    {notice && <div className="success-banner" role="status"><Icon name="check" size={18} /><span>{notice}</span><button className="link-btn" onClick={() => navigate('downloads')}>View downloads<Icon name="arrowRight" size={14} /></button></div>}
    {loading && <div className="collection-empty search-state" role="status"><span className="empty-symbol"><Icon name="search" size={29} /></span><h2>Searching {source === 'all' ? 'all enabled websites' : sourceName(source)}…</h2><p>Finding videos for “{query}”.</p></div>}
    {error && <div className="error-banner" role="alert"><span>{error}</span><button className="link-btn" onClick={() => setRetry((value) => value + 1)}>Try again</button></div>}
    {!loading && response?.warnings?.map((warning) => <div className="search-warning" role="status" key={`${warning.source}:${warning.message}`}><Icon name={recommendationProviderIcon(warning.source)} size={18} /><span><strong>{sourceName(warning.source)}:</strong> {warning.message}</span></div>)}
    {!loading && !error && !query && providersReady && sourceAvailable && <div className="collection-empty search-state"><span className="empty-symbol"><Icon name="search" size={30} /></span><h2>Your next favorite is out there.</h2><p>Try a topic, a creator, or the name of a video.<br />Choose all enabled websites or search just one.</p><div className="search-platforms">{providers.map((provider) => <span key={provider.id}><Icon name={recommendationProviderIcon(provider.id)} size={18} />{provider.name}</span>)}</div></div>}
    {!loading && !error && response && <section aria-label="Video search results">
      <div className="section-title search-results-heading"><div><h2>Results for “{response.query}”<span className="count-pill">{response.results.length}</span></h2><p>Add a video to download it. Watch it here once it’s ready.</p></div>
        {response.results.length > 0 && <div className="search-result-tools"><button type="button" className="btn-secondary" disabled={savingAll} onClick={saveAllForLater}><Icon name="folder" size={15} />{savingAll ? 'Savingâ€¦' : 'Save all to Watch later'}</button><label className="quality-label">Download quality<select aria-label="Search download quality" value={quality} onChange={(event) => setQuality(event.target.value)}><option value="best">Best available</option><option value="1080p">1080p</option><option value="720p">720p</option><option value="480p">480p</option></select></label></div>}
      </div>
      {saveAllError && <div className="error-banner" role="alert"><span>{saveAllError}</span></div>}
      {response.results.length ? <><div className="media-grid">{response.results.map((video) => <SearchResult key={video.source_url} video={video} providerName={settings.providers.find((provider) => provider.id === video.connector)?.name || video.provider_name || sourceName(video.connector)} download={downloads[video.source_url]} onAdd={addVideo} linkReview={linkReviews[video.source_url]} onMarkRepeated={markRepeated} />)}</div>{response.has_more && limit < 24 && <div className="search-load-more"><button type="button" className="btn-secondary" onClick={() => setPage({ key: baseKey, limit: 24 })}><Icon name="plus" size={15} />Load more links</button></div>}</> : <div className="collection-empty search-state" role="status"><span className="empty-symbol"><Icon name="search" size={29} /></span><h2>No videos found</h2><p>Try different keywords or choose another website.</p></div>}
    </section>}
  </div>;
}
