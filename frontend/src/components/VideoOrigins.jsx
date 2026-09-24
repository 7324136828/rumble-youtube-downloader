import React from 'react';
import './WatchLater.css';

export default function VideoOrigins({ video }) {
  const origins = new Set(Array.isArray(video.origins) ? video.origins : []);
  if (video.user_added) origins.add('watch_later');
  const names = { custom_search: 'Website search', public_search: 'Legacy discovery', watch_later: 'Saved by you' };
  const labels = [...origins].filter((origin) => names[origin]);
  return labels.length ? <span className="video-origins">{labels.map((origin) => <span key={origin} className={origin === 'watch_later' ? 'is-saved' : ''}>{names[origin]}</span>)}</span> : null;
}
