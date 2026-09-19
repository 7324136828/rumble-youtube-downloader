import React, { useEffect, useState } from 'react';
import { requestVideoStream } from '../services/api';
import CustomVideoPlayer from './CustomVideoPlayer';

function formatSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function VideoCard({ video }) {
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    setPlaying(false);
  }, [video.stream_url, video.state]);

  const prepare = async () => {
    try {
      await requestVideoStream(video.stream_url);
    } catch {
      /* preparation continues server-side; next poll picks it up */
    }
  };

  const ready = video.state === 'ready';

  return (
    <div className="video-card">
      <div className="video-card-title" title={video.name}>
        {video.name}
        <span className="muted"> {formatSize(video.size)}</span>
      </div>
      {video.job_name && <div className="muted video-card-job">{video.job_name}</div>}
      {playing && ready ? (
        <CustomVideoPlayer src={video.stream_url} title={video.name} autoPlay />
      ) : ready ? (
        <button className="btn-continue" onClick={() => setPlaying(true)}>
          Play
        </button>
      ) : video.state === 'preparing' ? (
        <span className="badge badge-in_progress">Preparing video…</span>
      ) : (
        <button className="btn-continue" onClick={prepare}>
          Prepare for playback
        </button>
      )}
    </div>
  );
}
