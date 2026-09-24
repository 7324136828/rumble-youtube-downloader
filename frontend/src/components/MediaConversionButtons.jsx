import React, { useEffect, useRef, useState } from 'react';
import { getMediaItem, startMediaConversion } from '../services/api';
import Icon from './Icon';
import './MediaConversionButtons.css';

const EMPTY = {
  mp4: { format: 'mp4', status: 'idle' },
  mp3: { format: 'mp3', status: 'idle' },
};

function isWorking(item) {
  return item?.status === 'queued' || item?.status === 'converting';
}

export default function MediaConversionButtons({ video, formats = ['mp4', 'mp3'], onMp4Ready, compact = false }) {
  const [conversions, setConversions] = useState(() => ({ ...EMPTY, ...(video?.conversions || {}) }));
  const [requestError, setRequestError] = useState('');
  const notifiedMp4 = useRef('');

  useEffect(() => {
    setConversions({ ...EMPTY, ...(video?.conversions || {}) });
    setRequestError('');
    notifiedMp4.current = '';
  }, [video?.id]);

  const working = formats.some((format) => isWorking(conversions[format]));
  useEffect(() => {
    if (!working || !video?.id) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const current = await getMediaItem(video.id);
        if (!cancelled) {
          setConversions((previous) => ({ ...previous, ...(current.conversions || {}) }));
          setRequestError('');
        }
      } catch (error) {
        if (!cancelled) setRequestError(error.message || 'Could not check conversion progress.');
      }
    };
    const timer = setInterval(poll, 1000);
    poll();
    return () => { cancelled = true; clearInterval(timer); };
  }, [working, video?.id, formats.join(',')]);

  const mp4 = conversions.mp4;
  useEffect(() => {
    if (mp4?.status !== 'completed' || !mp4.stream_url || notifiedMp4.current === mp4.stream_url) return;
    notifiedMp4.current = mp4.stream_url;
    onMp4Ready?.(mp4.stream_url);
  }, [mp4?.status, mp4?.stream_url, onMp4Ready]);

  const start = async (format) => {
    setRequestError('');
    setConversions((previous) => ({
      ...previous,
      [format]: { ...previous[format], format, status: 'queued', error_message: null },
    }));
    try {
      const next = await startMediaConversion(video.id, format);
      setConversions((previous) => ({ ...previous, [format]: next }));
    } catch (error) {
      setConversions((previous) => ({ ...previous, [format]: { ...previous[format], format, status: 'failed' } }));
      setRequestError(error.message || `Could not convert this video to ${format.toUpperCase()}.`);
    }
  };

  return <div className={`media-conversion-actions${compact ? ' media-conversion-actions--compact' : ''}`}>
    {formats.map((format) => {
      const item = conversions[format] || EMPTY[format];
      const upper = format.toUpperCase();
      if (item.status === 'completed' && item.download_url) {
        return <a key={format} className="media-conversion-button is-complete" href={item.download_url} download aria-label={`${upper} conversion complete. Download ${upper}`}>
          <Icon name="check" size={16} /> {upper} complete <Icon name="download" size={15} />
        </a>;
      }
      if (isWorking(item)) {
        return <button key={format} type="button" className="media-conversion-button is-working" disabled aria-label={`Converting video to ${upper}`}>
          <Icon name="refresh" size={16} /> Converting to {upper}...
        </button>;
      }
      return <button key={format} type="button" className="media-conversion-button" onClick={() => start(format)} aria-label={format === 'mp4' ? 'Convert video to compatible MP4' : 'Download MP3'} title={item.error_message || undefined}>
        <Icon name={format === 'mp4' ? 'refresh' : 'download'} size={16} /> {format === 'mp4' ? 'Convert to MP4' : 'Download MP3'}
      </button>;
    })}
    {requestError && <span className="media-conversion-error" role="alert">{requestError}</span>}
  </div>;
}
