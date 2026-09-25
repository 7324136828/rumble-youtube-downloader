import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { PLAYBACK_SPEEDS, updatePlaybackPreferences, usePlaybackPreferences } from '../playbackPreferences';
import './CustomVideoPlayer.css';

function PlayerIcon({ name, size = 20 }) {
  const paths = {
    play: <path d="m9 5 11 7-11 7Z" fill="currentColor" stroke="none" />,
    pause: <><path d="M7 5h3v14H7zM14 5h3v14h-3z" fill="currentColor" stroke="none" /></>,
    volume: <><path d="M11 4 6 8H3v8h3l5 4Z" /><path d="M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></>,
    mute: <><path d="M11 4 6 8H3v8h3l5 4Z" /><path d="m16 9 6 6m0-6-6 6" /></>,
    fullscreen: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
    collapse: <path d="M3 8h5V3m8 0v5h5M8 21v-5H3m13 5v-5h5" />,
    pip: <><rect x="2" y="4" width="20" height="16" rx="2" /><path d="M12 11h7v6h-7z" fill="currentColor" stroke="none" /></>,
    previous: <><path d="m17 5-10 7 10 7Z" fill="currentColor" stroke="none" /><path d="M5 5v14" /></>,
    next: <><path d="m7 5 10 7-10 7Z" fill="currentColor" stroke="none" /><path d="M19 5v14" /></>,
    retry: <><path d="M20 7v5h-5" /><path d="M20 12a8 8 0 1 0-2 5M20 7l-3-2" /></>,
    alert: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6m0 3v.1" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function timeLabel(value) {
  const seconds = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours ? `${hours}:` : ''}${hours ? String(minutes).padStart(2, '0') : minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

/** Custom controls around an HTML video. The forwarded ref is the video element. */
const CustomVideoPlayer = forwardRef(function CustomVideoPlayer({
  src, poster, title = 'Video', autoPlay = false, active = true, audioOnly = false,
  muted: controlledMuted, onMutedChange, loop = false, variant = 'watch',
  onEnded, onNext, onPrevious, onTimeUpdate, onLoadedMetadata, formatErrorActions, children,
}, forwardedRef) {
  const videoRef = useRef(null);
  const rootRef = useRef(null);
  const hideTimer = useRef(null);
  const requestId = useRef(0);
  const mounted = useRef(false);
  const activeRef = useRef(active);
  const muteCallback = useRef(onMutedChange);
  const preferences = usePlaybackPreferences();
  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [volume, setVolume] = useState(1);
  const speed = preferences.speed;
  const [visible, setVisible] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pipAvailable, setPipAvailable] = useState(false);
  const [pipActive, setPipActive] = useState(false);
  const [fullscreenAvailable, setFullscreenAvailable] = useState(false);
  const isMuted = controlledMuted ?? preferences.muted;

  activeRef.current = active;
  muteCallback.current = onMutedChange;
  useImperativeHandle(forwardedRef, () => videoRef.current, []);

  const changeMuted = useCallback((value) => {
    if (videoRef.current) videoRef.current.muted = value;
    updatePlaybackPreferences({ muted: value });
    muteCallback.current?.(value);
  }, []);

  const requestPlay = useCallback(async () => {
    const video = videoRef.current;
    if (!video || !activeRef.current) return;
    const id = ++requestId.current;
    const valid = () => mounted.current && id === requestId.current && activeRef.current;
    setNotice('');
    try {
      await video.play();
      if (!mounted.current || !activeRef.current) video.pause();
    } catch (playError) {
      if (!valid() || playError?.name === 'AbortError') return;
      if (valid()) {
        setPlaying(false);
        setBuffering(false);
        setVisible(true);
        setNotice(playError?.name === 'NotAllowedError' ? (video.muted ? 'Press play to start' : 'Press play to start with sound') : 'Playback could not start. Try again.');
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const video = videoRef.current;
    const standardPip = Boolean(video?.requestPictureInPicture
      && document.pictureInPictureEnabled !== false);
    let webkitPip = false;
    try {
      webkitPip = Boolean(video?.webkitSetPresentationMode
        && (!video.webkitSupportsPresentationMode
          || video.webkitSupportsPresentationMode('picture-in-picture')));
    } catch { /* Safari can reject capability checks before media is ready. */ }
    setPipAvailable(standardPip || webkitPip);
    setFullscreenAvailable(Boolean(
      (rootRef.current?.requestFullscreen && document.fullscreenEnabled !== false)
      || video?.webkitEnterFullscreen));
    const updateFullscreen = () => setFullscreen(document.fullscreenElement === rootRef.current);
    const enterWebkitFullscreen = () => setFullscreen(true);
    const leaveWebkitFullscreen = () => setFullscreen(false);
    const enterPip = () => setPipActive(true);
    const leavePip = () => setPipActive(false);
    const updateWebkitPresentation = () => setPipActive(video?.webkitPresentationMode === 'picture-in-picture');
    document.addEventListener('fullscreenchange', updateFullscreen);
    video?.addEventListener('webkitbeginfullscreen', enterWebkitFullscreen);
    video?.addEventListener('webkitendfullscreen', leaveWebkitFullscreen);
    video?.addEventListener('enterpictureinpicture', enterPip);
    video?.addEventListener('leavepictureinpicture', leavePip);
    video?.addEventListener('webkitpresentationmodechanged', updateWebkitPresentation);
    return () => {
      mounted.current = false;
      requestId.current += 1;
      clearTimeout(hideTimer.current);
      document.removeEventListener('fullscreenchange', updateFullscreen);
      video?.removeEventListener('webkitbeginfullscreen', enterWebkitFullscreen);
      video?.removeEventListener('webkitendfullscreen', leaveWebkitFullscreen);
      video?.removeEventListener('enterpictureinpicture', enterPip);
      video?.removeEventListener('leavepictureinpicture', leavePip);
      video?.removeEventListener('webkitpresentationmodechanged', updateWebkitPresentation);
      video?.pause();
    };
  }, []);

  useEffect(() => {
    requestId.current += 1;
    setCurrentTime(0);
    setDuration(0);
    setBuffered(0);
    setError('');
    setNotice('');
    setPipActive(false);
    setPlaying(false);
    setBuffering(false);
    setVisible(true);
  }, [src]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = isMuted;
  }, [isMuted]);

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.defaultPlaybackRate = speed;
      videoRef.current.playbackRate = speed;
    }
  }, [speed, src]);

  useEffect(() => {
    const video = videoRef.current;
    if (!active) {
      requestId.current += 1;
      video?.pause();
      setBuffering(false);
    } else if (autoPlay && src) {
      requestPlay();
    }
    return () => { requestId.current += 1; video?.pause(); };
  }, [active, autoPlay, src, requestPlay]);

  const revealControls = useCallback(() => {
    setVisible(true);
    clearTimeout(hideTimer.current);
    if (!videoRef.current?.paused) hideTimer.current = setTimeout(() => setVisible(false), 2600);
  }, []);

  useEffect(() => {
    revealControls();
    return () => clearTimeout(hideTimer.current);
  }, [playing, revealControls]);

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video || !active) return;
    if (video.paused || video.ended) requestPlay();
    else { requestId.current += 1; video.pause(); }
    revealControls();
  };

  const seek = (value) => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
    video.currentTime = Math.max(0, Math.min(video.duration, value));
    setCurrentTime(video.currentTime);
    onTimeUpdate?.(video.currentTime, video.duration);
    revealControls();
  };

  const changeVolume = (value) => {
    const next = Math.max(0, Math.min(1, value));
    if (videoRef.current) videoRef.current.volume = next;
    setVolume(next);
    changeMuted(next === 0);
  };

  const toggleFullscreen = async () => {
    const video = videoRef.current;
    try {
      if (document.fullscreenElement === rootRef.current) await document.exitFullscreen();
      else if (rootRef.current?.requestFullscreen && document.fullscreenEnabled !== false) await rootRef.current.requestFullscreen();
      else if (fullscreen && video?.webkitExitFullscreen) video.webkitExitFullscreen();
      else if (video?.webkitEnterFullscreen) video.webkitEnterFullscreen();
      else throw new Error('Fullscreen unavailable');
    } catch { setNotice('Fullscreen is unavailable in this browser.'); }
    revealControls();
  };

  const togglePip = async () => {
    const video = videoRef.current;
    try {
      if (document.pictureInPictureElement === video) await document.exitPictureInPicture();
      else if (video?.requestPictureInPicture && document.pictureInPictureEnabled !== false) await video.requestPictureInPicture();
      else if (video?.webkitSetPresentationMode) video.webkitSetPresentationMode(
        video.webkitPresentationMode === 'picture-in-picture' ? 'inline' : 'picture-in-picture');
      else throw new Error('Picture-in-picture unavailable');
    } catch { setNotice('Picture-in-picture is unavailable for this video.'); }
  };

  const handleKeyboard = (event) => {
    if (!active || event.altKey || event.ctrlKey || event.metaKey || event.target.isContentEditable || event.target.closest('input, select, textarea, button, a, [contenteditable="true"]')) return;
    const key = event.key.toLowerCase();
    let handled = true;
    if (key === ' ' || key === 'k') togglePlay();
    else if (key === 'm') changeMuted(!videoRef.current?.muted);
    else if (key === 'f' && fullscreenAvailable) toggleFullscreen();
    else if (key === 'arrowleft' || key === 'j') seek((videoRef.current?.currentTime || 0) - (key === 'j' ? 10 : 5));
    else if (key === 'arrowright' || key === 'l') seek((videoRef.current?.currentTime || 0) + (key === 'l' ? 10 : 5));
    else if (key === 'arrowdown' && variant === 'feed' && onNext) onNext();
    else if (key === 'arrowup' && variant === 'feed' && onPrevious) onPrevious();
    else if (key === 'arrowup' && variant !== 'feed') changeVolume(volume + 0.1);
    else if (key === 'arrowdown' && variant !== 'feed') changeVolume(volume - 0.1);
    else handled = false;
    if (handled) { event.preventDefault(); event.stopPropagation(); revealControls(); }
  };

  const updateDuration = (video) => setDuration(Number.isFinite(video.duration) ? video.duration : 0);
  const updateBuffer = (video) => {
    if (!Number.isFinite(video.duration) || !video.duration) return;
    for (let i = 0; i < video.buffered.length; i += 1) {
      if (video.buffered.start(i) <= video.currentTime && video.buffered.end(i) >= video.currentTime) {
        setBuffered((video.buffered.end(i) / video.duration) * 100);
        return;
      }
    }
    setBuffered(0);
  };

  const handleError = () => {
    const code = videoRef.current?.error?.code;
    setBuffering(false);
    setPlaying(false);
    setError(code === 3 || code === 4
      ? 'This video format cannot be played in your browser. Download it or import a browser-compatible MP4.'
      : 'The video could not be loaded. Check your connection and try again.');
  };

  const retry = () => {
    setError('');
    setNotice('');
    videoRef.current?.load();
    requestPlay();
  };

  const progress = duration ? Math.max(0, Math.min(100, currentTime / duration * 100)) : 0;
  const showControls = visible || !playing || Boolean(error);
  const controlButton = (label, icon, action, className = '') => (
    <button type="button" className={`cvp-button ${className}`} aria-label={label} title={label} onClick={action}>
      <PlayerIcon name={icon} />
    </button>
  );

  return (
    <div ref={rootRef} className={`cvp cvp--${variant}${audioOnly ? ' cvp--audio' : ''}${showControls ? ' cvp--controls-visible' : ''}${!active ? ' cvp--inactive' : ''}`} role="region" aria-label={`${title} ${audioOnly ? 'audio' : 'video'} player`} tabIndex={active ? 0 : -1} onKeyDown={handleKeyboard} onPointerMove={revealControls} onFocus={revealControls}>
      <video ref={videoRef} className="cvp-video" src={src || undefined} poster={poster || undefined} muted={isMuted} loop={loop} playsInline preload={active ? 'metadata' : 'none'} tabIndex={-1}
        onPlay={(event) => { if (!activeRef.current) { event.currentTarget.pause(); return; } setPlaying(true); setNotice(''); }}
        onPlaying={(event) => { if (!activeRef.current) { event.currentTarget.pause(); return; } setPlaying(true); setBuffering(false); }}
        onPause={() => { setPlaying(false); setBuffering(false); }}
        onWaiting={() => { if (active && !videoRef.current?.paused) setBuffering(true); }}
        onSeeking={() => { if (active && !videoRef.current?.paused) setBuffering(true); }}
        onSeeked={() => setBuffering(false)}
        onCanPlay={() => setBuffering(false)}
        onLoadedMetadata={(event) => { updateDuration(event.currentTarget); event.currentTarget.playbackRate = speed; onLoadedMetadata?.(event); }}
        onDurationChange={(event) => updateDuration(event.currentTarget)}
        onTimeUpdate={(event) => { const video = event.currentTarget; setCurrentTime(video.currentTime); updateBuffer(video); onTimeUpdate?.(video.currentTime, Number.isFinite(video.duration) ? video.duration : 0); }}
        onProgress={(event) => updateBuffer(event.currentTarget)}
        onVolumeChange={(event) => setVolume(event.currentTarget.volume)}
        onEnded={(event) => { setPlaying(false); setBuffering(false); onEnded?.(event); }}
        onError={handleError}
      />
      {audioOnly && <div className="cvp-audio-artwork" aria-hidden="true">{poster ? <img src={poster} alt="" /> : <span className="cvp-audio-placeholder">♫</span>}<span className="cvp-audio-label">Audio</span></div>}
      {!error && <button type="button" className="cvp-surface" tabIndex={-1} aria-label={playing ? 'Pause video' : 'Play video'} onClick={() => { rootRef.current?.focus({ preventScroll: true }); togglePlay(); }} disabled={!active}>
        {!playing && !buffering && <span className="cvp-big-play"><PlayerIcon name="play" size={30} /></span>}
      </button>}
      {buffering && !error && <div className="cvp-buffering" role="status" aria-label="Buffering video"><span /></div>}
      {children && <div className="cvp-overlay">{children}</div>}
      {notice && !error && <div className="cvp-notice" role="status">{notice}</div>}
      {error && <div className="cvp-error" role="alert"><PlayerIcon name="alert" size={30} /><p>{error}</p>{formatErrorActions}<button type="button" className="cvp-retry" onClick={retry}><PlayerIcon name="retry" size={16} /> Try again</button></div>}
      <div className="cvp-controls" aria-label="Playback controls" onPointerEnter={revealControls}>
        <input className="cvp-seek" type="range" min="0" max={duration || 1} step="0.1" value={Math.min(currentTime, duration || 1)} disabled={!duration || !active} onChange={(event) => seek(Number(event.target.value))} aria-label="Seek video" aria-valuetext={`${timeLabel(currentTime)} of ${timeLabel(duration)}`} style={{ '--cvp-progress': `${progress}%`, '--cvp-buffered': `${buffered}%` }} />
        <div className="cvp-control-row">
          {controlButton(playing ? 'Pause (K)' : 'Play (K)', playing ? 'pause' : 'play', togglePlay)}
          {onPrevious && controlButton('Previous video', 'previous', onPrevious, 'cvp-skip')}
          {onNext && controlButton('Next video', 'next', onNext, 'cvp-skip')}
          <div className="cvp-volume-group">
            {controlButton(isMuted || volume === 0 ? 'Unmute (M)' : 'Mute (M)', isMuted || volume === 0 ? 'mute' : 'volume', () => { if (volume === 0) changeVolume(0.5); else changeMuted(!isMuted); })}
            <input className="cvp-volume" type="range" min="0" max="1" step="0.05" value={isMuted ? 0 : volume} onChange={(event) => changeVolume(Number(event.target.value))} aria-label="Volume" aria-valuetext={`${Math.round((isMuted ? 0 : volume) * 100)} percent`} />
          </div>
          <span className="cvp-time"><span>{timeLabel(currentTime)}</span><span className="cvp-time-divider"> / </span><span className="cvp-duration">{timeLabel(duration)}</span></span>
          <span className="cvp-spacer" />
          <select className="cvp-speed" aria-label="Playback speed" title="Playback speed" value={speed} onChange={(event) => updatePlaybackPreferences({ speed: Number(event.target.value) })}>
            {PLAYBACK_SPEEDS.map((rate) => <option key={rate} value={rate}>{rate}x</option>)}
          </select>
          {pipAvailable && !audioOnly && controlButton(pipActive ? 'Exit picture-in-picture' : 'Picture-in-picture', 'pip', togglePip, 'cvp-pip')}
          {fullscreenAvailable && controlButton(fullscreen ? 'Exit fullscreen (F)' : 'Fullscreen (F)', fullscreen ? 'collapse' : 'fullscreen', toggleFullscreen)}
        </div>
      </div>
    </div>
  );
});

export default CustomVideoPlayer;


