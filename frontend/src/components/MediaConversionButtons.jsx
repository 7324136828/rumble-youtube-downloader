import React, { useEffect, useRef, useState } from 'react';
import { getMediaItem, startMediaConversion, updateMediaPlayback } from '../services/api';
import { mediaPlayback } from '../mediaUtils';
import Icon from './Icon';
import './MediaConversionButtons.css';

const EMPTY = { mp4: { format: 'mp4', status: 'idle' }, mp3: { format: 'mp3', status: 'idle' } };
const isWorking = (item) => item?.status === 'queued' || item?.status === 'converting';

export default function MediaConversionButtons({ video, formats = ['mp4', 'mp3'], onMp4Ready, onMediaUpdated, compact = false }) {
  const [conversions, setConversions] = useState(() => ({ ...EMPTY, ...(video?.conversions || {}) }));
  const [playbackFormat, setPlaybackFormat] = useState(video?.playback_format || 'original');
  const [requestError, setRequestError] = useState('');
  const [switching, setSwitching] = useState(false);
  const notifiedMp4 = useRef('');
  const currentId = useRef(video?.id);
  const currentVideo = useRef(video);
  const updateCallback = useRef(onMediaUpdated);
  const requests = useRef(new Set());
  currentId.current = video?.id;
  currentVideo.current = video;
  updateCallback.current = onMediaUpdated;

  useEffect(() => {
    setRequestError('');
    setSwitching(false);
    notifiedMp4.current = '';
    requests.current.clear();
  }, [video?.id]);
  useEffect(() => {
    setConversions({ ...EMPTY, ...(video?.conversions || {}) });
    setPlaybackFormat(video?.playback_format || 'original');
  }, [video?.id, video?.conversions, video?.playback_format]);

  const applyMedia = (current) => {
    if (!current || current.id !== currentId.current) return;
    setConversions({ ...EMPTY, ...(current.conversions || {}) });
    setPlaybackFormat(current.playback_format || 'original');
    updateCallback.current?.(current);
  };
  const applyConversion = (id, format, conversion) => {
    if (id !== currentId.current) return;
    applyMedia({ ...currentVideo.current, conversions: { ...currentVideo.current?.conversions, [format]: conversion } });
  };

  const working = formats.some((format) => isWorking(conversions[format]));
  useEffect(() => {
    if (!working || !video?.id) return undefined;
    let cancelled = false;
    let pending = false;
    const poll = async () => {
      if (pending || formats.some((format) => requests.current.has(format))) return;
      pending = true;
      try {
        const current = await getMediaItem(video.id);
        if (!cancelled) { applyMedia(current); setRequestError(''); }
      } catch (error) {
        if (!cancelled) setRequestError(error.message || 'Could not check conversion progress.');
      } finally { pending = false; }
    };
    const timer = setInterval(poll, 1000);
    // The conversion POST can still be in flight. Let it publish its queued state first.
    return () => { cancelled = true; clearInterval(timer); };
  }, [working, video?.id, formats.join(',')]);

  const mp4 = conversions.mp4;
  useEffect(() => {
    if (mp4?.status !== 'completed' || !mp4.stream_url || notifiedMp4.current === mp4.stream_url) return;
    notifiedMp4.current = mp4.stream_url;
    onMp4Ready?.(mp4.stream_url);
  }, [mp4?.status, mp4?.stream_url, onMp4Ready]);

  const start = async (format) => {
    const id = video.id;
    if (requests.current.has(format)) return;
    requests.current.add(format);
    setRequestError('');
    applyConversion(id, format, { ...conversions[format], format, status: 'queued', error_message: null });
    try {
      const next = await startMediaConversion(id, format);
      if (id !== currentId.current) return;
      applyConversion(id, format, next);
      if (next.status === 'completed') {
        try { applyMedia(await getMediaItem(id)); }
        catch (error) { if (id === currentId.current) setRequestError(error.message || 'Conversion finished, but playback could not be refreshed.'); }
      }
    } catch (error) {
      if (id !== currentId.current) return;
      applyConversion(id, format, { format, status: 'failed' });
      setRequestError(error.message || `Could not convert this media to ${format.toUpperCase()}.`);
    } finally { if (id === currentId.current) requests.current.delete(format); }
  };

  const switchPlayback = async (format) => {
    const id = video.id;
    if (requests.current.has('playback')) return;
    requests.current.add('playback');
    setSwitching(true);
    setRequestError('');
    try { applyMedia(await updateMediaPlayback(id, format)); }
    catch (error) { if (id === currentId.current) setRequestError(error.message || 'Could not change playback format.'); }
    finally { if (id === currentId.current) { requests.current.delete('playback'); setSwitching(false); } }
  };

  return <div className={`media-conversion-actions${compact ? ' media-conversion-actions--compact' : ''}`}>
    {formats.map((format) => {
      const item = conversions[format] || EMPTY[format];
      const upper = format.toUpperCase();
      const audioMp4 = format === 'mp4' && video?.media_kind === 'audio';
      const missingArtwork = audioMp4 && !video?.thumbnail_url;
      if (item.status === 'completed' && (item.stream_url || item.download_url)) {
        const current = { ...video, conversions, playback_format: playbackFormat };
        const audioSelected = format === 'mp3'
          && mediaPlayback(current).src === item.stream_url;
        return <React.Fragment key={format}>
          {format === 'mp3' && video?.media_kind !== 'audio' && item.stream_url && <button type="button" className="media-conversion-button is-complete" disabled={switching} onClick={() => switchPlayback(audioSelected ? 'original' : 'mp3')}><Icon name="play" size={16} />{switching ? 'Switching...' : audioSelected ? 'Play video' : 'Play MP3'}</button>}
          {item.download_url && <a className="media-conversion-button is-complete" href={item.download_url} download aria-label={`${upper} conversion complete. Download ${upper}`}><Icon name="download" size={15} /> Download {upper}</a>}
        </React.Fragment>;
      }
      if (isWorking(item)) return <button key={format} type="button" className="media-conversion-button is-working" disabled aria-label={`Converting video to ${upper}`}><Icon name="refresh" size={16} /> Converting to {upper}...</button>;
      return <button key={format} type="button" className="media-conversion-button" disabled={missingArtwork} onClick={() => start(format)} aria-label={audioMp4 ? 'Create MP4 with thumbnail' : format === 'mp4' ? 'Convert video to compatible MP4' : 'Convert to MP3'} title={missingArtwork ? 'Add a custom thumbnail first' : item.error_message || undefined}><Icon name="refresh" size={16} /> {audioMp4 ? 'Create MP4 with thumbnail' : `Convert to ${upper}`}</button>;
    })}
    {formats.filter((format) => conversions[format]?.status === 'failed' && conversions[format]?.error_message).map((format) => <span key={`${format}-error`} className="media-conversion-error" role="alert">{format.toUpperCase()} conversion failed: {conversions[format].error_message}</span>)}
    {requestError && <span className="media-conversion-error" role="alert">{requestError}</span>}
  </div>;
}
