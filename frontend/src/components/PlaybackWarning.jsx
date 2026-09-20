import React from 'react';
import Icon from './Icon';
import './PlaybackWarning.css';

export default function PlaybackWarning({ video }) {
  if (!video?.playback_warning) return null;
  return <div className="playback-warning" role="status"><Icon name="info" size={18} /><p>{video.playback_warning}{video.download_url && <> <a href={video.download_url} download>Download to play externally</a></>}</p></div>;
}
