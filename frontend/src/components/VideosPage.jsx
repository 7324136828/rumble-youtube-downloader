import React, { useEffect, useState } from 'react';
import { getAllVideos } from '../services/api';
import VideoCard from './VideoCard';

export default function VideosPage() {
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await getAllVideos();
        if (!cancelled) setVideos(data);
      } catch {
        /* transient poll errors ignored */
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const timer = setInterval(load, 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="videos-screen">
      <h2>Video Library</h2>
      {loading ? (
        <p className="muted">Loading videos…</p>
      ) : videos.length === 0 ? (
        <p className="muted">
          No videos yet. Enable "Keep video" when converting to make videos
          playable here.
        </p>
      ) : (
        <div className="video-grid">
          {videos.map((video) => (
            <VideoCard key={`${video.job_id}:${video.path}`} video={video} />
          ))}
        </div>
      )}
    </div>
  );
}
