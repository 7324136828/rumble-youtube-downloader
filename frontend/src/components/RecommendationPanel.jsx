import React, { useEffect, useRef, useState } from 'react';
import { getMediaItem, getRecommendations, startDownloads } from '../services/api';
import { connectorBadgeClass, connectorLabel, formatDuration, setPlayerMode } from '../mediaUtils';
import { downloadStatusLabel } from '../downloadUtils';
import { useRecommendations } from './RecommendationContext';
import Icon from './Icon';
import './Recommendations.css';

function Suggestion({ video, download, busy, onPlay }) {
  const [imageFailed, setImageFailed] = useState(false);
  const pending = download && ['starting', 'downloading'].includes(download.status);
  return <li className="recommendation-item">
    <div className="recommendation-thumbnail">
      {video.thumbnail_url && !imageFailed ? <img src={video.thumbnail_url} alt="" loading="lazy" onError={() => setImageFailed(true)} /> : <Icon name={video.connector} size={28} />}
      {formatDuration(video.duration) && <span>{formatDuration(video.duration)}</span>}
    </div>
    <div className="recommendation-item-body">
      <span className={connectorBadgeClass(video.connector)}>{connectorLabel(video.connector)}</span>
      <h3>{video.title}</h3><p>{video.uploader || connectorLabel(video.connector)}</p>
      {video.reason && <p className="recommendation-reason">{video.reason}</p>}
      <button className={video.media_id ? 'btn-secondary' : 'btn-primary'} disabled={pending || busy} onClick={() => onPlay(video)}>
        <Icon name={video.media_id ? 'play' : 'download'} size={14} />
        {pending ? download.status === 'starting' ? 'Adding to downloads...' : downloadStatusLabel({ ...download, status: download.mediaStatus }) : download?.error ? 'Retry download & play' : video.media_id ? 'Play' : 'Download & play'}
      </button>
      {pending && <p className="recommendation-download-state" role="status">Playback starts when ready. The download also appears in Downloads.</p>}
      {download?.error && <p className="recommendation-download-error" role="alert">{download.error}</p>}
    </div>
  </li>;
}

export default function RecommendationPanel({ context = 'watch', videoId = null, excludeUrls = [], navigate, onReady }) {
  const { enabled, settings, revision, openSettings } = useRecommendations();
  const [result, setResult] = useState(null);
  const [requestError, setRequestError] = useState(null);
  const [loadingKey, setLoadingKey] = useState(null);
  const [refreshRequest, setRefreshRequest] = useState({ key: '', count: 0 });
  const [download, setDownload] = useState(null);
  const playback = useRef(null);
  const liveKey = useRef('');
  const latestCallbacks = useRef({ navigate, onReady });
  latestCallbacks.current = { navigate, onReady };
  const exclusionKey = JSON.stringify([...new Set(excludeUrls.filter(Boolean))].sort().slice(0, 100));
  const scope = JSON.stringify([context, videoId, exclusionKey, revision, settings.model_id, enabled]);
  const refreshCount = refreshRequest.key === scope ? refreshRequest.count : 0;
  const key = `${scope}:${refreshCount}`;
  liveKey.current = key;

  useEffect(() => {
    const controller = new AbortController();
    if (!enabled || !settings.model_id) return () => controller.abort();
    setLoadingKey(key);
    setResult(null);
    setRequestError(null);
    getRecommendations({ context, video_id: videoId, exclude_urls: JSON.parse(exclusionKey), limit: 8, refresh: refreshCount > 0 }, controller.signal).then((data) => {
      if (!controller.signal.aborted && liveKey.current === key) setResult({ key, data });
    }).catch((err) => {
      if (!controller.signal.aborted && liveKey.current === key) setRequestError({ key, message: err.message || 'Recommendations are temporarily unavailable.' });
    }).finally(() => {
      if (!controller.signal.aborted && liveKey.current === key) setLoadingKey(null);
    });
    return () => controller.abort();
  }, [enabled, settings.model_id, context, videoId, exclusionKey, key, refreshCount]);

  useEffect(() => {
    setDownload(null);
    return () => {
      if (playback.current) {
        playback.current.cancelled = true;
        clearTimeout(playback.current.timer);
        playback.current = null;
      }
    };
  }, [key]);

  const refresh = () => setRefreshRequest({ key: scope, count: refreshCount + 1 });
  const play = async (video) => {
    if (!enabled || playback.current) return;
    const operation = { key, cancelled: false, timer: null, polls: 0 };
    playback.current = operation;
    const valid = () => !operation.cancelled && liveKey.current === operation.key;
    const fail = (message) => {
      if (!valid()) return;
      playback.current = null;
      setDownload({ key, url: video.source_url, status: 'failed', error: message });
    };
    const open = (media) => {
      if (!valid()) return;
      playback.current = null;
      setPlayerMode(context);
      latestCallbacks.current.onReady?.(media);
      if (latestCallbacks.current.navigate) latestCallbacks.current.navigate(`${context}/${media.id}`);
      else window.location.hash = `${context}/${media.id}`;
    };
    const poll = async (id) => {
      if (!valid()) return;
      try {
        const media = await getMediaItem(id);
        if (!valid()) return;
        if (media.status === 'ready') { open(media); return; }
        if (media.status === 'failed') { fail(media.error_message || media.error || 'This download failed. Try again or choose another video.'); return; }
        setDownload({ key, url: video.source_url, status: 'downloading', mediaStatus: media.status, stage: media.stage, progress: media.progress });
        if (++operation.polls >= 600) { fail('This video is still being prepared. Check Downloads for its progress.'); return; }
        operation.timer = setTimeout(() => poll(id), 2000);
      } catch (err) { fail(err.message || 'Could not check this download. Find it in Downloads or try again.'); }
    };
    setDownload({ key, url: video.source_url, status: 'starting' });
    try {
      if (video.media_id) {
        const media = await getMediaItem(video.media_id);
        if (!valid()) return;
        if (media.status === 'ready') { open(media); return; }
        throw new Error('This saved video is no longer ready. Refresh recommendations to try again.');
      }
      const started = await startDownloads([video.source_url], 'best');
      if (!valid()) return;
      const media = started?.[0];
      if (!media?.id) throw new Error('The video could not be added to Downloads. Please try again.');
      if (media.status === 'ready') { open(media); return; }
      await poll(media.id);
    } catch (err) { fail(err.message || 'Could not download this video. Please try again.'); }
  };

  if (!enabled) return null;
  const data = result?.key === key ? result.data : null;
  const error = requestError?.key === key ? requestError.message : '';
  const loading = settings.model_id && (!data && !error || loadingKey === key);
  const visibleDownload = download?.key === key ? download : null;
  const busy = visibleDownload && ['starting', 'downloading'].includes(visibleDownload.status);
  return <section className={`recommendation-panel recommendation-panel-${context}`} aria-label={context === 'feed' ? 'Recommended videos for your feed' : 'AI suggested videos to play next'}>
    <div className="recommendation-panel-heading"><div><span className="eyebrow">SELECTED FOR YOU</span><h2>{context === 'feed' ? 'Discover your next video' : 'AI picks for you'}</h2></div><button className="icon-btn" aria-label="Refresh recommendations" onClick={refresh} disabled={Boolean(loading || busy)}><Icon name="refresh" size={17} /></button></div>
    <p className="recommendation-panel-intro">Ideas from your history and interests, selected by your model.</p>
    {!settings.model_id ? <div className="recommendation-state"><p>Choose a model configuration to start discovering videos.</p><button className="btn-secondary" onClick={openSettings}>Set up recommendations</button></div>
      : loading ? <div className="recommendation-state" role="status"><span className="cf-loading-ring" /><p>Finding videos you might like...</p></div>
        : error ? <div className="recommendation-state" role="alert"><p>{error}</p><div className="recommendation-form-actions"><button className="btn-secondary" onClick={refresh}>Try again</button><button className="link-btn" onClick={openSettings}>Model settings</button></div></div>
          : data?.status === 'needs_history' ? <div className="recommendation-state" role="status"><Icon name="history" size={25} /><h3>A little inspiration to get started</h3><p>Watch a video or add a few interests to receive recommendations.</p><button className="btn-secondary" onClick={openSettings}>Add your interests</button></div>
            : data?.status === 'disabled' ? <p className="recommendation-help">Recommendations have been turned off.</p>
              : data?.items?.length ? <ol className="recommendation-list">{data.items.map((video) => <Suggestion key={video.source_url} video={video} download={visibleDownload?.url === video.source_url ? visibleDownload : null} busy={Boolean(busy && visibleDownload.url !== video.source_url)} onPlay={play} />)}</ol>
                : <div className="recommendation-state" role="status"><p>No new suggestions right now. Try refreshing or add more interests.</p><button className="btn-secondary" onClick={openSettings}>Update interests</button></div>}
    {data?.warnings?.map((warning, index) => <p key={`${index}:${warning}`} className="recommendation-warning" role="status">{warning}</p>)}
    {data?.keywords?.length > 0 && <p className="recommendation-keywords">Inspired by: {data.keywords.join(', ')}</p>}
  </section>;
}
