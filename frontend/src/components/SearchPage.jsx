import React, { useEffect, useRef, useState } from 'react';
import { searchVideos, startDownloads } from '../services/api';
import { connectorBadgeClass, connectorLabel, formatDuration } from '../mediaUtils';
import Icon from './Icon';
import VideoSearchForm, { videoSearchRoute } from './VideoSearchForm';

function SearchResult({ video, download, onAdd }) {
  const [imageFailed, setImageFailed] = useState(false);
  const adding = download?.status === 'adding';
  const added = download?.status === 'added';
  return <article className="media-card search-result">
    <a className="media-thumb" href={video.source_url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${video.title} on ${connectorLabel(video.connector)} (new tab)`}>
      {video.thumbnail_url && !imageFailed ? <img src={video.thumbnail_url} alt="" loading="lazy" onError={() => setImageFailed(true)} /> : <span className={`media-thumb-fallback source-${video.connector}`}><Icon name={video.connector} size={42} /></span>}
      <span className={`${connectorBadgeClass(video.connector)} thumb-source`}>{connectorLabel(video.connector)}</span>
      <span className="thumb-play"><Icon name="external" size={22} /></span>
      {formatDuration(video.duration) && <span className="thumb-duration">{formatDuration(video.duration)}</span>}
    </a>
    <div className="media-card-body">
      <h3 title={video.title}>{video.title}</h3>
      <p>{video.uploader || connectorLabel(video.connector)}</p>
      <div className="search-result-actions">
        <button className={added ? 'btn-secondary search-added' : 'btn-primary'} disabled={adding || added} onClick={() => onAdd(video)}>
          <Icon name={added ? 'check' : 'plus'} size={15} />{added ? 'Added to queue' : adding ? 'Adding…' : 'Add to library'}
        </button>
        <a className="card-open" href={video.source_url} target="_blank" rel="noopener noreferrer">Open original<Icon name="external" size={14} /><span className="sr-only"> (new tab)</span></a>
      </div>
      {download?.error && <p className="search-download-error" role="alert">{download.error}</p>}
    </div>
  </article>;
}

export default function SearchPage({ query = '', source = 'all', navigate }) {
  const [response, setResponse] = useState(null);
  const [loading, setLoading] = useState(Boolean(query));
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [quality, setQuality] = useState('best');
  const [downloads, setDownloads] = useState({});
  const [notice, setNotice] = useState('');
  const queued = useRef(new Set());

  useEffect(() => {
    const controller = new AbortController();
    setResponse(null);
    setError('');
    setLoading(Boolean(query));
    if (query) {
      searchVideos(query, source, controller.signal).then((result) => {
        if (!controller.signal.aborted) setResponse(result);
      }).catch((err) => {
        if (!controller.signal.aborted) setError(err.message || 'Video search is temporarily unavailable.');
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    }
    return () => controller.abort();
  }, [query, source, retry]);

  const search = (nextQuery, nextSource) => {
    if (nextQuery === query && nextSource === source) setRetry((value) => value + 1);
    else navigate(videoSearchRoute(nextQuery, nextSource));
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

  return <div className="page-wrap search-page">
    <div className="page-heading"><div>
      <p className="eyebrow">FIND SOMETHING WORTH WATCHING</p>
      <h1>Search videos<span className="accent">.</span></h1>
      <p className="page-description">Discover videos on YouTube and Rumble. Save your favorites to watch here.</p>
    </div></div>
    <VideoSearchForm query={query} source={source} onSearch={search} />
    <p className="video-search-hint">Search by keyword, then choose a video to add to your library.</p>
    {notice && <div className="success-banner" role="status"><Icon name="check" size={18} /><span>{notice}</span><button className="link-btn" onClick={() => navigate('downloads')}>View downloads<Icon name="arrowRight" size={14} /></button></div>}
    {loading && <div className="collection-empty search-state" role="status"><span className="empty-symbol"><Icon name="search" size={29} /></span><h2>Searching {source === 'all' ? 'YouTube and Rumble' : connectorLabel(source)}…</h2><p>Finding videos for “{query}”.</p></div>}
    {error && <div className="error-banner" role="alert"><span>{error}</span><button className="link-btn" onClick={() => setRetry((value) => value + 1)}>Try again</button></div>}
    {!loading && response?.warnings?.map((warning) => <div className="search-warning" role="status" key={warning.source}><Icon name="globe" size={18} /><span><strong>{connectorLabel(warning.source)}:</strong> {warning.message}</span></div>)}
    {!loading && !error && !query && <div className="collection-empty search-state"><span className="empty-symbol"><Icon name="search" size={30} /></span><h2>Your next favorite is out there.</h2><p>Try a topic, a creator, or the name of a video.<br />Choose both platforms or search just one.</p><div className="search-platforms"><span className="source-youtube"><Icon name="youtube" size={18} />YouTube</span><span className="source-rumble"><Icon name="rumble" size={18} />Rumble</span></div></div>}
    {!loading && !error && response && <section aria-label="Video search results">
      <div className="section-title search-results-heading"><div><h2>Results for “{response.query}”<span className="count-pill">{response.results.length}</span></h2><p>Add a video to download it. Watch it here once it’s ready.</p></div>
        {response.results.length > 0 && <label className="quality-label">Download quality<select aria-label="Search download quality" value={quality} onChange={(event) => setQuality(event.target.value)}><option value="best">Best available</option><option value="1080p">1080p</option><option value="720p">720p</option><option value="480p">480p</option></select></label>}
      </div>
      {response.results.length ? <div className="media-grid">{response.results.map((video) => <SearchResult key={video.source_url} video={video} download={downloads[video.source_url]} onAdd={addVideo} />)}</div> : <div className="collection-empty search-state" role="status"><span className="empty-symbol"><Icon name="search" size={29} /></span><h2>No videos found</h2><p>Try different keywords or choose another platform.</p></div>}
    </section>}
  </div>;
}
