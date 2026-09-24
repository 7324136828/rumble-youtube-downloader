import React, { useEffect, useRef, useState } from 'react';
import { getMediaItem, getRecommendations, recordRecommendationImpressions, startDownloads, updateLinkState } from '../services/api';
import { connectorBadgeClass, formatDuration, setPlayerMode } from '../mediaUtils';
import { recommendationProviderIcon, recommendationProviderLabel } from '../recommendationUtils';
import { downloadStatusLabel } from '../downloadUtils';
import { useRecommendations } from './RecommendationContext';
import WatchLaterButton from './WatchLaterButton';
import VideoOrigins from './VideoOrigins';
import Icon from './Icon';
import './Recommendations.css';

function impressionItems(videos) {
  const encoder = new TextEncoder();
  return videos.slice(0, 20).flatMap((video) => {
    const source = video.source_url;
    if (typeof source !== 'string' || source.length > 2048 || /[\u0000-\u001f]/.test(source)) return [];
    try {
      const url = new URL(source);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return [];
    } catch { return []; }
    const item = { source_url: source };
    for (const [field, limit] of Object.entries({ id: 2048, title: 500, connector: 253, uploader: 500, description: 2000, thumbnail_url: 2048 })) {
      const value = video[field];
      if (typeof value !== 'string') continue;
      // Identifiers and URLs must stay intact; long optional values are omitted.
      if (['id', 'thumbnail_url'].includes(field)) {
        if (value.length <= limit) item[field] = value;
      } else item[field] = value.slice(0, limit);
    }
    // Twenty items fit within the durable activity payload's 256 KB limit,
    // including unusually large Unicode metadata and JSON escaping.
    for (const field of ['description', 'thumbnail_url', 'id', 'uploader', 'title', 'connector']) {
      if (encoder.encode(JSON.stringify(item)).length <= 12000) break;
      delete item[field];
    }
    return [item];
  });
}

function Suggestion({ video, download, onAction, providerName }) {
  const [imageFailed, setImageFailed] = useState(false);
  const [repeatState, setRepeatState] = useState('idle');
  const [repeatError, setRepeatError] = useState('');
  const pending = download && ['starting', 'downloading'].includes(download.status);
  const playable = Boolean(video.media_id || download?.media?.id);
  const showOriginal = video.downloadable === false || !['youtube', 'rumble'].includes(video.connector);
  const label = pending
    ? download.status === 'starting' ? 'Adding to downloads...' : downloadStatusLabel({ ...download, status: download.mediaStatus })
    : download?.error ? 'Retry download' : playable ? 'Play' : 'Download';
  const markRepeated = async () => {
    if (!video.link_id || repeatState !== 'idle') return;
    setRepeatState('saving'); setRepeatError('');
    try { await updateLinkState(video.link_id, 'silenced'); setRepeatState('saved'); }
    catch (err) { setRepeatState('idle'); setRepeatError(err.message || 'Could not mark this link as repeated.'); }
  };
  return <li className="recommendation-item">
    <div className="recommendation-thumbnail">
      {video.thumbnail_url && !imageFailed ? <img src={video.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} /> : <Icon name={recommendationProviderIcon(video.connector)} size={28} />}
      {formatDuration(video.duration) && <span>{formatDuration(video.duration)}</span>}
    </div>
    <div className="recommendation-item-body">
      <span className={connectorBadgeClass(video.connector)}>{providerName}</span>
      {video.verified === false && <span className="recommendation-unverified">Unverified link</span>}
      <VideoOrigins video={video} />
      <h3>{video.title}</h3><p>{video.uploader || providerName}</p>
      {video.reason && <p className="recommendation-reason">{video.reason}</p>}
      <div className="recommendation-item-actions">
        {(playable || video.downloadable !== false) && <button className={playable ? 'btn-secondary' : 'btn-primary'} disabled={pending} onClick={() => onAction(video, download)}>
          <Icon name={playable ? 'play' : 'download'} size={14} />{label}
        </button>}
        {showOriginal && <a className="btn-secondary" href={video.source_url} target="_blank" rel="noopener noreferrer"><Icon name="external" size={14} />Open original</a>}
        <WatchLaterButton video={video} />
        {video.link_id && <button type="button" className="btn-secondary" disabled={repeatState !== 'idle'} onClick={markRepeated}><Icon name={repeatState === 'saved' ? 'check' : 'history'} size={14} />{repeatState === 'saved' ? 'Marked repeated' : repeatState === 'saving' ? 'Savingâ€¦' : 'Mark repeated'}</button>}
      </div>
      {pending && <p className="recommendation-download-state" role="status">This download also appears in Downloads. You can start another recommendation while it finishes.</p>}
      {download?.status === 'ready' && <p className="recommendation-download-state" role="status">Download complete. It is ready when you want to play it.</p>}
      {download?.error && <p className="recommendation-download-error" role="alert">{download.error}</p>}
      {repeatError && <p className="recommendation-download-error" role="alert">{repeatError}</p>}
    </div>
  </li>;
}

function SearchDetails({ sources, warnings = [] }) {
  if (!sources.length) return null;
  const displayedWarnings = new Set(warnings);
  return <details className="recommendation-search-details">
    <summary>Search details <span>{sources.length} {sources.length === 1 ? 'website' : 'websites'}</span></summary>
    <ul>
      {sources.map((source) => {
        const count = Number.isFinite(source.count) ? Math.max(0, Math.floor(source.count)) : 0;
        const videos = `${count} ${count === 1 ? 'video' : 'videos'}`;
        const status = source.status === 'ok' ? `Found ${videos}`
          : source.status === 'empty' ? `No matches · ${videos}`
            : source.status === 'cached' ? `Using recent results · ${videos}`
              : `Search temporarily unavailable · ${videos}`;
        const discovery = ['provider_search', 'native', 'custom_search'].includes(source.discovery) ? 'Website search' : '';
        const details = (Array.isArray(source.warnings) ? source.warnings : []).filter((warning) => {
          if (typeof warning !== 'string' || !warning || displayedWarnings.has(warning)) return false;
          displayedWarnings.add(warning);
          return true;
        });
        return <li key={source.id} data-source={source.id}>
          <div className="recommendation-source-heading"><strong>{source.name}</strong>{discovery && <span>{discovery}</span>}</div>
          <p className={`recommendation-source-status ${source.status === 'unavailable' ? 'is-unavailable' : ''}`}>{status}</p>
          {details.map((warning) => <p className="recommendation-source-note" key={warning}>{warning}</p>)}
        </li>;
      })}
    </ul>
  </details>;
}

export default function RecommendationPanel({ context = 'watch', videoId = null, excludeUrls = [], source, navigate, onReady }) {
  const { enabled, settings, revision, openSettings } = useRecommendations();
  const [result, setResult] = useState(null);
  const [requestError, setRequestError] = useState(null);
  const [loadingKey, setLoadingKey] = useState(null);
  const [refreshRequest, setRefreshRequest] = useState({ key: '', count: 0 });
  const [downloads, setDownloads] = useState({});
  const [providerFilter, setProviderFilter] = useState('all');
  const operations = useRef(new Map());
  const liveKey = useRef('');
  const reportedImpressions = useRef(new WeakSet());
  const latestCallbacks = useRef({ navigate, onReady });
  latestCallbacks.current = { navigate, onReady };
  const selectedSource = source ?? providerFilter;
  const sourceAvailable = settings.providers.some((provider) => provider.enabled && (selectedSource === 'all' || provider.id === selectedSource));
  const sourceLabel = recommendationProviderLabel(selectedSource, settings.providers);
  const exclusionKey = JSON.stringify([...new Set(excludeUrls.filter(Boolean))].sort().slice(0, 100));
  const scope = JSON.stringify([context, selectedSource, videoId, exclusionKey, revision, settings.model_id, enabled]);
  const refreshCount = refreshRequest.key === scope ? refreshRequest.count : 0;
  const key = `${scope}:${refreshCount}`;
  liveKey.current = key;

  useEffect(() => {
    const controller = new AbortController();
    if (!enabled || !settings.model_id || !sourceAvailable) return () => controller.abort();
    setLoadingKey(key);
    setResult(null);
    setRequestError(null);
    getRecommendations({ context, source: selectedSource, video_id: videoId, exclude_urls: JSON.parse(exclusionKey), limit: 8, refresh: refreshCount > 0 }, controller.signal).then((data) => {
      if (!controller.signal.aborted && liveKey.current === key) setResult({ key, data });
    }).catch((err) => {
      if (!controller.signal.aborted && liveKey.current === key) setRequestError({ key, message: err.message || 'Recommendations are temporarily unavailable.' });
    }).finally(() => {
      if (!controller.signal.aborted && liveKey.current === key) setLoadingKey(null);
    });
    return () => controller.abort();
  }, [enabled, settings.model_id, sourceAvailable, context, selectedSource, videoId, exclusionKey, key, refreshCount]);

  useEffect(() => {
    if (!enabled || !settings.model_id || !sourceAvailable || result?.key !== key
      || loadingKey === key || requestError?.key === key || !result.data.items?.length
      || ['disabled', 'needs_history'].includes(result.data.status)) return;
    const report = () => {
      if (document.visibilityState === 'hidden' || liveKey.current !== key || reportedImpressions.current.has(result)) return;
      reportedImpressions.current.add(result);
      const items = impressionItems(result.data.items);
      if (!items.length) return;
      const eventId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      recordRecommendationImpressions({ event_id: eventId, context, items }).catch(() => {
        // Activity logging must not interrupt browsing or recommendation playback.
      });
    };
    report();
    document.addEventListener('visibilitychange', report);
    return () => document.removeEventListener('visibilitychange', report);
  }, [enabled, settings.model_id, sourceAvailable, result, key, context, loadingKey, requestError]);

  useEffect(() => {
    setDownloads({});
    return () => {
      for (const operation of operations.current.values()) {
        operation.cancelled = true;
        clearTimeout(operation.timer);
      }
      operations.current.clear();
    };
  }, [key]);

  const refresh = () => setRefreshRequest({ key: scope, count: refreshCount + 1 });
  const updateDownload = (url, value) => setDownloads((previous) => ({ ...previous, [url]: { ...value, key } }));
  const open = (video, download) => {
    const mediaId = video.media_id || download?.media?.id;
    if (!mediaId) return;
    setPlayerMode(context === 'feed' ? 'feed' : 'watch');
    const target = context === 'feed' ? 'feed' : 'watch';
    if (latestCallbacks.current.navigate) latestCallbacks.current.navigate(`${target}/${mediaId}`);
    else window.location.hash = `${target}/${mediaId}`;
  };
  const downloadVideo = async (video) => {
    if (!enabled || operations.current.has(video.source_url)) return;
    const operation = { key, cancelled: false, timer: null, polls: 0 };
    operations.current.set(video.source_url, operation);
    const valid = () => !operation.cancelled && liveKey.current === operation.key;
    const finish = (value) => {
      operations.current.delete(video.source_url);
      if (valid()) updateDownload(video.source_url, value);
    };
    const fail = (message) => finish({ status: 'failed', error: message });
    const poll = async (id) => {
      if (!valid()) return;
      try {
        const media = await getMediaItem(id);
        if (!valid()) return;
        if (media.status === 'ready') {
          finish({ status: 'ready', media });
          return;
        }
        if (media.status === 'failed') { fail(media.error_message || media.error || 'This download failed. Try again or choose another video.'); return; }
        updateDownload(video.source_url, { status: 'downloading', mediaStatus: media.status, stage: media.stage, progress: media.progress });
        if (++operation.polls >= 600) { fail('This video is still being prepared. Check Downloads for its progress.'); return; }
        operation.timer = setTimeout(() => poll(id), 2000);
      } catch (err) { fail(err.message || 'Could not check this download. Find it in Downloads or try again.'); }
    };
    updateDownload(video.source_url, { status: 'starting' });
    try {
      const started = await startDownloads([video.source_url], 'best');
      if (!valid()) return;
      const media = started?.[0];
      if (!media?.id) throw new Error('The video could not be added to Downloads. Please try again.');
      if (media.status === 'ready') {
        finish({ status: 'ready', media });
        return;
      }
      await poll(media.id);
    } catch (err) { fail(err.message || 'Could not download this video. Please try again.'); }
  };
  const action = (video, download) => {
    if (video.media_id || download?.media?.id) open(video, download);
    else downloadVideo(video);
  };

  if (!enabled) return null;
  const data = result?.key === key ? result.data : null;
  const error = requestError?.key === key ? requestError.message : '';
  const loading = settings.model_id && sourceAvailable && ((!data && !error) || loadingKey === key);
  const searchedSources = (Array.isArray(data?.sources) ? data.sources : []).filter((entry) => entry && settings.providers.some((provider) => provider.enabled && provider.id === entry.id) && (selectedSource === 'all' || selectedSource === entry.id));
  return <section className={`recommendation-panel recommendation-panel-${context}`} aria-label={context === 'feed' ? 'Recommended videos for your feed' : context === 'history' ? 'Recommendations based on watch history' : 'AI suggested videos to play next'}>
    <div className="recommendation-panel-heading"><div><span className="eyebrow">SELECTED FOR YOU</span><h2>{context === 'feed' ? 'Discover your next video' : context === 'history' ? `${selectedSource === 'all' ? '' : sourceLabel + ' '}recommendations` : 'AI picks for you'}</h2></div><button className="icon-btn" aria-label="Refresh recommendations" onClick={refresh} disabled={Boolean(loading) || !sourceAvailable}><Icon name="refresh" size={17} /></button></div>
    <p className="recommendation-panel-intro">Ideas from your history and interests, selected by your model{selectedSource === 'all' ? ' across all your enabled websites.' : ` from ${sourceLabel}.`}</p>
    {data?.fallback_used && <p className="recommendation-fallback-note" role="status">Your model did not return usable picks. These videos were chosen at random from website search and Watch later using your fallback percentages.</p>}
    {source == null && <label className="recommendation-field recommendation-source-filter">Recommendation websites
      <select aria-label="Recommendation websites" value={selectedSource} onChange={(event) => setProviderFilter(event.target.value)}>
        <option value="all">All enabled websites</option>
        {selectedSource !== 'all' && !sourceAvailable && <option value={selectedSource} disabled>{sourceLabel} (not enabled)</option>}
        {settings.providers.filter((provider) => provider.enabled).map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}
      </select>
    </label>}
    {!settings.model_id ? <div className="recommendation-state"><p>Choose a model configuration to start discovering videos.</p><button className="btn-secondary" onClick={openSettings}>Set up recommendations</button></div>
      : !sourceAvailable ? <div className="recommendation-state" role="status"><p>{selectedSource === 'all' ? 'Enable at least one website to receive recommendations.' : `Enable ${sourceLabel} in recommendation settings to discover videos from this website.`}</p><button className="btn-secondary" onClick={openSettings}>Choose websites</button></div>
        : loading ? <div className="recommendation-state" role="status"><span className="cf-loading-ring" /><p>Finding videos you might like...</p></div>
        : error ? <div className="recommendation-state" role="alert"><p>{error}</p><div className="recommendation-form-actions"><button className="btn-secondary" onClick={refresh}>Try again</button><button className="link-btn" onClick={openSettings}>Model settings</button></div></div>
          : data?.status === 'needs_history' ? <div className="recommendation-state" role="status"><Icon name="history" size={25} /><h3>A little inspiration to get started</h3><p>Watch a video or add a few interests to receive recommendations.</p><button className="btn-secondary" onClick={openSettings}>Add your interests</button></div>
            : data?.status === 'disabled' ? <p className="recommendation-help">Recommendations have been turned off.</p>
              : data?.items?.length ? <ol className="recommendation-list">{data.items.map((video) => <Suggestion key={video.source_url} video={video} providerName={recommendationProviderLabel(video.connector, settings.providers)} download={downloads[video.source_url]?.key === key ? downloads[video.source_url] : null} onAction={action} />)}</ol>
                : <div className="recommendation-state" role="status"><p>No new suggestions right now. Try refreshing or add more interests.</p><button className="btn-secondary" onClick={openSettings}>Update interests</button></div>}
    {data?.warnings?.map((warning, index) => <p key={`${index}:${warning}`} className="recommendation-warning" role="status">{warning}</p>)}
    <SearchDetails sources={searchedSources} warnings={data?.warnings} />
    {data?.keywords?.length > 0 && <p className="recommendation-keywords">Inspired by: {data.keywords.join(', ')}</p>}
  </section>;
}
