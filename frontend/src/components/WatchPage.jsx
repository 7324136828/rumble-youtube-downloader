import React, { useEffect, useRef, useState } from 'react';
import { deleteMedia, getMediaItems } from '../services/api';
import { connectorBadgeClass, connectorLabel, formatDuration, formatSize, loadLikes, saveLikes, setPlayerMode, toggleLike } from '../mediaUtils';
import CustomVideoPlayer from './CustomVideoPlayer';
import Icon from './Icon';
import './FeedWatch.css';

function UpNextItem({ video, onOpen, first }) {
  return <button className="cf-upnext-item" onClick={() => onOpen(video.id)}><span className="cf-upnext-thumb">{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" loading="lazy" /> : <Icon name="play" size={25} />}{video.duration ? <span>{formatDuration(video.duration)}</span> : null}{first && <i>UP NEXT</i>}</span><span className="cf-upnext-info"><strong>{video.title || video.file_name || 'Saved video'}</strong><span>{video.uploader || connectorLabel(video.connector)}</span><small>{connectorLabel(video.connector)} <span>·</span> Saved to library</small></span></button>;
}
function readAutoplay() {
  try { return localStorage.getItem('clipfeed.autoplay') !== '0'; } catch { return true; }
}
function rememberPosition(id, time) {
  try { sessionStorage.setItem(`clipfeed.position.${id}`, String(time)); } catch { /* Storage is optional. */ }
}

export default function WatchPage({ videoId, navigate }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [likes, setLikes] = useState(loadLikes);
  const [autoplay, setAutoplay] = useState(readAutoplay);
  const [expanded, setExpanded] = useState(false);
  const [notice, setNotice] = useState('');
  const [deleting, setDeleting] = useState(false);
  const playerRef = useRef(null);
  const noticeTimer = useRef(null);
  const lastSecond = useRef(-1);

  useEffect(() => {
    let cancelled = false;
    setError('');
    getMediaItems('ready').then((list) => {
      if (!cancelled) setItems(list);
    }).catch((err) => {
      if (!cancelled) setError(err.message || 'The video library could not be loaded.');
    });
    return () => { cancelled = true; };
  }, [videoId, retry]);
  useEffect(() => { setExpanded(false); lastSecond.current = -1; }, [videoId]);
  useEffect(() => () => clearTimeout(noticeTimer.current), []);

  const video = items ? (videoId ? items.find((item) => item.id === videoId) : items[0]) || null : undefined;
  const currentIndex = video ? items.findIndex((item) => item.id === video.id) : -1;
  const upnext = video ? [...items.slice(currentIndex + 1), ...items.slice(0, currentIndex)] : [];
  const nextVideo = items?.[currentIndex + 1];
  const openWatch = (id) => { setPlayerMode('watch'); navigate(`watch/${id}`); };
  const showNotice = (message) => {
    setNotice(message);
    clearTimeout(noticeTimer.current);
    noticeTimer.current = setTimeout(() => setNotice(''), 3500);
  };
  const toggleAutoplay = () => {
    const next = !autoplay;
    setAutoplay(next);
    try { localStorage.setItem('clipfeed.autoplay', next ? '1' : '0'); } catch { /* Session state still works. */ }
  };
  const openFeed = () => {
    if (video && playerRef.current) rememberPosition(video.id, playerRef.current.currentTime);
    setPlayerMode('feed'); navigate(video ? `feed/${video.id}` : 'feed');
  };
  const copySource = async () => {
    try {
      if (!video.source_url || !navigator.clipboard?.writeText) throw new Error('unavailable');
      await navigator.clipboard.writeText(video.source_url);
      showNotice('Source link copied');
    } catch { showNotice('Could not copy the link. Use “View original” below to open the source.'); }
  };
  const removeVideo = async () => {
    if (!video || !window.confirm('Delete this video and its downloaded files?')) return;
    setDeleting(true);
    try {
      await deleteMedia(video.id);
      setItems((previous) => previous.filter((item) => item.id !== video.id));
      const nextLikes = new Set(likes);
      nextLikes.delete(video.id); saveLikes(nextLikes); setLikes(nextLikes);
      if (upnext[0]) openWatch(upnext[0].id); else navigate('library');
    } catch (err) { showNotice(`Could not delete video: ${err.message}`); }
    finally { setDeleting(false); }
  };

  return (
    <section className="cf-watch-page">
      <header className="cf-view-heading"><div><div className="cf-eyebrow">MAKE TIME FOR THE GOOD STUFF</div><h1>Watch<span className="cf-heading-dot">.</span></h1><p>Settle in. It's your screen.</p></div><div className="cf-mode-switch" aria-label="Player view"><button aria-pressed="false" onClick={openFeed}><Icon name="feed" size={17} /> Feed</button><button className="active" aria-pressed="true"><Icon name="watch" size={17} /> Watch</button></div></header>
      {error ? <div className="cf-view-state" role="alert"><div className="cf-state-icon"><Icon name="refresh" size={30} /></div><h2>We couldn't load this video</h2><p>{error}</p><button className="btn-primary" onClick={() => setRetry((value) => value + 1)}><Icon name="refresh" size={16} /> Try again</button></div>
        : video === undefined ? <div className="cf-view-state" role="status"><span className="cf-loading-ring" /><p>Loading your videos...</p></div>
        : video === null ? <div className="cf-view-state"><div className="cf-state-icon"><Icon name="watch" size={32} /></div><div className="cf-eyebrow">YOUR FRONT ROW SEAT</div><h2>{videoId ? 'This video is not ready to watch.' : 'Something worth watching starts here.'}</h2><p>{videoId ? 'It may still be downloading or may have been removed. Check your library for its status.' : 'Add videos to your library, then enjoy them in a player made for you.'}</p><button className="btn-primary" onClick={() => navigate('library')}><Icon name={videoId ? 'library' : 'plus'} size={17} />{videoId ? 'Open library' : 'Add your first video'}</button></div>
        : <div className="cf-watch-layout">
          <div className="cf-watch-primary">
            <CustomVideoPlayer ref={playerRef} key={video.id} src={video.stream_url} poster={video.thumbnail_url || undefined} title={video.title || video.file_name || 'Saved video'} autoPlay variant="watch" onNext={nextVideo ? () => openWatch(nextVideo.id) : undefined} onEnded={() => { rememberPosition(video.id, 0); if (autoplay && nextVideo) openWatch(nextVideo.id); }} onLoadedMetadata={(event) => { try { const time = Number(sessionStorage.getItem(`clipfeed.position.${video.id}`)); const element = event.currentTarget; if (Number.isFinite(time) && time > 0 && time < element.duration - 1) element.currentTime = time; } catch { /* Start at the beginning if storage is unavailable. */ } }} onTimeUpdate={(time) => { const second = Math.floor(time); if (lastSecond.current !== second) { lastSecond.current = second; rememberPosition(video.id, time); } }} />
            <div className="cf-watch-title-row"><span className={connectorBadgeClass(video.connector)}>{connectorLabel(video.connector)}</span><span className="cf-watch-saved"><Icon name="check" size={13} /> Saved to your library</span></div>
            <h2 className="cf-watch-title">{video.title || video.file_name || 'Saved video'}</h2>
            <div className="cf-watch-details"><div className="cf-creator-avatar">{(video.uploader || connectorLabel(video.connector)).charAt(0).toUpperCase()}</div><div className="cf-creator-info"><strong>{video.uploader || connectorLabel(video.connector)}</strong><span>{[formatDuration(video.duration), video.width && video.height ? `${video.width} × ${video.height}` : '', formatSize(video.file_size)].filter(Boolean).join(' · ') || 'Downloaded video'}</span></div><button className="cf-watch-feed-button" onClick={openFeed}><Icon name="feed" size={16} /> Open in feed</button></div>
            <div className="cf-watch-actions"><button className={likes.has(video.id) ? 'is-liked' : ''} aria-pressed={likes.has(video.id)} onClick={() => setLikes((previous) => toggleLike(previous, video.id))}><Icon name="heart" size={17} />{likes.has(video.id) ? 'Liked' : 'Like'}</button>{video.download_url && <a href={video.download_url} download><Icon name="download" size={17} /> Download</a>}<button onClick={copySource}><Icon name="link" size={17} /> Copy link</button><button className="cf-watch-delete" onClick={removeVideo} disabled={deleting} aria-label="Delete this video"><Icon name="trash" size={17} />{deleting ? 'Deleting...' : 'Delete'}</button></div>
            <div className="cf-watch-description"><div className="cf-description-heading"><strong>About this video</strong>{video.source_url && <a href={video.source_url} target="_blank" rel="noopener noreferrer">View original <Icon name="external" size={13} /></a>}</div><p className={expanded ? 'expanded' : ''}>{video.description || 'Saved from ' + connectorLabel(video.connector) + ' to your personal library.'}</p>{video.description?.length > 220 && <button onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? 'Show less' : 'Show more'}</button>}</div>
          </div>
          <aside className="cf-watch-sidebar"><div className="cf-upnext-heading"><h3>Up next <span>{upnext.length}</span></h3><label className="cf-autoplay-toggle">Autoplay<input type="checkbox" checked={autoplay} onChange={toggleAutoplay} /><span aria-hidden="true" /></label></div><div className="cf-upnext-subtitle">More from your collection</div>{upnext.length ? upnext.map((item, index) => <UpNextItem key={item.id} video={item} onOpen={openWatch} first={index === 0 && Boolean(nextVideo)} />) : <div className="cf-upnext-empty"><Icon name="library" size={28} /><h4>Room for another favorite.</h4><p>Add more videos to keep watching.</p><button onClick={() => navigate('library')}><Icon name="plus" size={15} /> Add a video</button></div>}</aside>
        </div>}
      {notice && <div className="cf-view-toast" role="status">{notice}</div>}
    </section>
  );
}
