import React, { useCallback, useEffect, useRef, useState } from 'react';
import { deleteMedia, getMediaItems } from '../services/api';
import { connectorBadgeClass, connectorLabel, formatDuration, loadLikes, saveLikes, setPlayerMode, toggleLike } from '../mediaUtils';
import CustomVideoPlayer from './CustomVideoPlayer';
import RecommendationPanel from './RecommendationPanel';
import PlaybackWarning from './PlaybackWarning';
import MediaConversionButtons from './MediaConversionButtons';
import useWatchHistory from '../hooks/useWatchHistory';
import Icon from './Icon';
import './FeedWatch.css';

function readMuted() {
  try { return localStorage.getItem('clipfeed.muted') !== '0'; } catch { return true; }
}
function rememberPosition(id, time) {
  try { sessionStorage.setItem(`clipfeed.position.${id}`, String(time)); } catch { /* Storage is optional. */ }
}
function restorePosition(id, element) {
  try {
    const time = Number(sessionStorage.getItem(`clipfeed.position.${id}`));
    if (Number.isFinite(time) && time > 0 && time < element.duration - 1) element.currentTime = time;
  } catch { /* A fresh playback position is safe. */ }
}

export default function FeedPage({ videoId, navigate }) {
  const [videos, setVideos] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [loadedRevision, setLoadedRevision] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [muted, setMuted] = useState(readMuted);
  const [likes, setLikes] = useState(loadLikes);
  const [notice, setNotice] = useState('');
  const [deleting, setDeleting] = useState(null);
  const [streamOverrides, setStreamOverrides] = useState({});
  const containerRef = useRef(null);
  const itemRefs = useRef([]);
  const videoRefs = useRef(new Map());
  const videosRef = useRef([]);
  const activeRef = useRef(0);
  const positionSeconds = useRef({});
  const noticeTimer = useRef(null);
  videosRef.current = videos || [];
  activeRef.current = activeIndex;
  const getActivePlayer = useCallback((id) => videoRefs.current.get(id), []);
  const recommendationReady = useCallback((media) => {
    if (media?.status !== 'ready') return;
    setVideos((previous) => previous?.some((video) => video.id === media.id) ? previous.map((video) => video.id === media.id ? media : video) : [...previous || [], media]);
  }, []);

  const showNotice = useCallback((message) => {
    setNotice(message);
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(''), 3500);
  }, []);
  const goTo = useCallback((index, smooth = true) => {
    const list = videosRef.current;
    const container = containerRef.current;
    if (!container || !list.length) return;
    const next = Math.max(0, Math.min(index, list.length - 1));
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    container.scrollTo({ top: next * container.clientHeight, behavior: smooth && !reduceMotion ? 'smooth' : 'auto' });
    activeRef.current = next;
    setActiveIndex(next);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError('');
    getMediaItems('ready').then((items) => {
      if (!cancelled) { setVideos(items); setLoadedRevision((value) => value + 1); }
    }).catch((err) => {
      if (!cancelled) setError(err.message || 'The video library could not be loaded.');
    });
    return () => { cancelled = true; };
  }, [videoId, retry]);
  useEffect(() => {
    if (!videos?.length) return;
    const index = videoId ? videos.findIndex((video) => video.id === videoId) : 0;
    goTo(index >= 0 ? index : 0, false);
    if (videoId && index < 0) showNotice('That video is unavailable. Here is the rest of your feed.');
  }, [videoId, loadedRevision, Boolean(videos?.length), goTo, showNotice]);
  useEffect(() => {
    if (videos && activeIndex >= videos.length) goTo(Math.max(0, videos.length - 1), false);
  }, [videos, activeIndex, goTo]);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !videos?.length) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (!container.clientHeight || !entries.some((entry) => entry.isIntersecting && entry.intersectionRatio >= 0.65)) return;
      // Observer entries can predate a keyboard/wheel jump. Use the live scroll
      // position so a stale entry cannot reactivate the video we just left.
      const index = Math.max(0, Math.min(videosRef.current.length - 1, Math.round(container.scrollTop / container.clientHeight)));
      activeRef.current = index;
      setActiveIndex(index);
    }, { root: container, threshold: 0.65 });
    itemRefs.current.slice(0, videos.length).forEach((item) => item && observer.observe(item));
    const resizeObserver = new ResizeObserver(() => goTo(activeRef.current, false));
    resizeObserver.observe(container);
    return () => { observer.disconnect(); resizeObserver.disconnect(); };
  }, [videos, goTo]);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !videos?.length) return undefined;
    let accumulated = 0;
    let lockedUntil = 0;
    let resetTimer;
    const onWheel = (event) => {
      if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY) || event.target.closest('input')) return;
      event.preventDefault();
      if (Date.now() < lockedUntil) return;
      accumulated += event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? container.clientHeight : 1);
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => { accumulated = 0; }, 150);
      if (Math.abs(accumulated) >= 45) {
        goTo(activeRef.current + Math.sign(accumulated));
        accumulated = 0;
        lockedUntil = Date.now() + 550;
      }
    };
    container.addEventListener('wheel', onWheel, { passive: false });
    return () => { container.removeEventListener('wheel', onWheel); clearTimeout(resetTimer); };
  }, [videos?.length, goTo]);
  useEffect(() => {
    const onKey = (event) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.target.isContentEditable || event.target.closest('input, textarea, select, button, a, [contenteditable="true"]')) return;
      if (['ArrowDown', 'PageDown'].includes(event.key)) {
        event.preventDefault(); goTo(activeRef.current + 1);
      } else if (['ArrowUp', 'PageUp'].includes(event.key)) {
        event.preventDefault(); goTo(activeRef.current - 1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [goTo]);
  useEffect(() => () => clearTimeout(noticeTimer.current), []);

  const updateMuted = (next) => {
    setMuted(next);
    try { localStorage.setItem('clipfeed.muted', next ? '1' : '0'); } catch { /* Session state still works. */ }
  };
  const openWatch = (video) => {
    const element = videoRefs.current.get(video.id);
    if (element) rememberPosition(video.id, element.currentTime);
    setPlayerMode('watch'); navigate(`watch/${video.id}`);
  };
  const copySource = async (video) => {
    try {
      if (!video.source_url || !navigator.clipboard?.writeText) throw new Error('unavailable');
      await navigator.clipboard.writeText(video.source_url);
      showNotice('Source link copied');
    } catch { showNotice('Could not copy the link. Open the original video from Watch view.'); }
  };
  const removeVideo = async (video) => {
    if (!window.confirm('Delete this video and its downloaded files?')) return;
    setDeleting(video.id);
    try {
      await deleteMedia(video.id);
      setVideos((previous) => previous.filter((item) => item.id !== video.id));
      setLikes((previous) => { const next = new Set(previous); next.delete(video.id); saveLikes(next); return next; });
      showNotice('Video deleted');
    } catch (err) { showNotice(`Could not delete video: ${err.message}`); }
    finally { setDeleting(null); }
  };
  const activeVideo = videos?.[activeIndex];
  useWatchHistory(activeVideo?.id, getActivePlayer, !error);
  return (
    <section className="cf-feed-page">
      <header className="cf-view-heading">
        <div><div className="cf-eyebrow">YOUR PERSONAL CHANNEL</div><h1>The feed<span className="cf-heading-dot">.</span></h1><p>Your saved videos. One swipe away.</p></div>
        <div className="cf-mode-switch" aria-label="Player view"><button className="active" aria-pressed="true"><Icon name="feed" size={17} /> Feed</button><button aria-pressed="false" onClick={() => activeVideo ? openWatch(activeVideo) : navigate('watch')}><Icon name="watch" size={17} /> Watch</button></div>
      </header>
      {!error && <PlaybackWarning video={activeVideo} />}
      {error ? <div className="cf-view-state" role="alert"><div className="cf-state-icon"><Icon name="refresh" size={30} /></div><h2>We couldn't load your feed</h2><p>{error}</p><button className="btn-primary" onClick={() => setRetry((value) => value + 1)}><Icon name="refresh" size={16} /> Try again</button></div>
        : videos === null ? <div className="cf-view-state" role="status"><span className="cf-loading-ring" /><p>Getting your feed ready...</p></div>
        : !videos.length ? <div className="cf-view-state cf-feed-empty-state"><div className="cf-empty-stack" aria-hidden="true"><div /><div /><div><Icon name="play" size={34} /><span>Your next favorite</span></div></div><div className="cf-eyebrow">A FEED THAT'S ALL YOURS</div><h2>Start with a video you love.</h2><p>Save a video from YouTube, Rumble, or another supported platform. Then swipe through your own collection.</p><button className="btn-primary" onClick={() => navigate('library')}><Icon name="plus" size={17} /> Add your first video</button><span className="cf-empty-caption">Your collection, your pace.</span></div>
        : <div className="cf-feed-layout">
          <div className="cf-feed-context"><span className="cf-feed-live-dot" /><span>From your library</span><strong>{String(activeIndex + 1).padStart(2, '0')} <span>/ {String(videos.length).padStart(2, '0')}</span></strong></div>
          <div className="cf-feed-scroll" ref={containerRef} tabIndex={0} aria-label="Video feed. Swipe or use arrow keys to change videos.">
            {videos.map((video, index) => {
              const active = index === activeIndex;
              const near = Math.abs(index - activeIndex) <= 1;
              return <article className="cf-feed-item" key={video.id} ref={(element) => { itemRefs.current[index] = element; }} aria-label={`Video ${index + 1} of ${videos.length}: ${video.title || video.file_name || 'Untitled video'}`} inert={active ? undefined : ''}>
                <div className="cf-feed-stage">
                  {near ? <CustomVideoPlayer ref={(element) => { if (element) videoRefs.current.set(video.id, element); else videoRefs.current.delete(video.id); }} src={streamOverrides[video.id] || video.conversions?.mp4?.stream_url || video.stream_url} poster={video.thumbnail_url || undefined} title={video.title || video.file_name || 'Saved video'} autoPlay active={active} muted={muted} onMutedChange={updateMuted} loop variant="feed" formatErrorActions={<MediaConversionButtons video={video} onMp4Ready={(url) => setStreamOverrides((previous) => ({ ...previous, [video.id]: url }))} />} onLoadedMetadata={(event) => restorePosition(video.id, event.currentTarget)} onTimeUpdate={(time) => { const second = Math.floor(time); if (positionSeconds.current[video.id] !== second) { positionSeconds.current[video.id] = second; rememberPosition(video.id, time); } }}>
                    <div className="cf-feed-overlay"><span className={connectorBadgeClass(video.connector)}>{connectorLabel(video.connector)}</span><h2>{video.title || video.file_name || 'Saved video'}</h2><p>{video.uploader || connectorLabel(video.connector)}{video.duration ? ` · ${formatDuration(video.duration)}` : ''}</p></div>
                  </CustomVideoPlayer> : <div className="cf-feed-placeholder">{video.thumbnail_url && <img src={video.thumbnail_url} alt="" />}</div>}
                </div>
                <div className="cf-feed-actions">
                  <button className={likes.has(video.id) ? 'is-liked' : ''} aria-label={likes.has(video.id) ? 'Unlike video' : 'Like video'} aria-pressed={likes.has(video.id)} onClick={() => setLikes((previous) => toggleLike(previous, video.id))}><span><Icon name="heart" size={23} /></span><small>{likes.has(video.id) ? 'Liked' : 'Like'}</small></button>
                  <button onClick={() => openWatch(video)} aria-label="Open in Watch view"><span><Icon name="watch" size={23} /></span><small>Watch</small></button>
                  {video.download_url && <a href={video.download_url} download aria-label="Download video file"><span><Icon name="download" size={23} /></span><small>Download</small></a>}
                  <button onClick={() => copySource(video)} aria-label="Copy original video link"><span><Icon name="link" size={22} /></span><small>Copy link</small></button>
                  <button className="cf-feed-delete" disabled={deleting === video.id} onClick={() => removeVideo(video)} aria-label="Delete video"><span><Icon name="trash" size={20} /></span><small>{deleting === video.id ? 'Deleting' : 'Remove'}</small></button>
                </div>
              </article>;
            })}
          </div>
          <div className="cf-feed-navigation"><button onClick={() => goTo(activeIndex - 1)} disabled={activeIndex === 0} aria-label="Previous video"><Icon name="chevronUp" size={24} /></button><button onClick={() => goTo(activeIndex + 1)} disabled={activeIndex === videos.length - 1} aria-label="Next video"><Icon name="chevronDown" size={24} /></button><span>Scroll to explore</span></div>
          <div className="cf-feed-bottom-note"><Icon name="feed" size={14} /> Swipe or use <kbd>↑</kbd> <kbd>↓</kbd> to explore</div>
        </div>}
      {!error && videos !== null && (!videos.length || activeIndex === videos.length - 1) && <RecommendationPanel context="feed" videoId={activeVideo?.id || null} excludeUrls={activeVideo?.source_url ? [activeVideo.source_url] : []} navigate={navigate} onReady={recommendationReady} />}
      {notice && <div className="cf-view-toast" role="status">{notice}</div>}
    </section>
  );
}



