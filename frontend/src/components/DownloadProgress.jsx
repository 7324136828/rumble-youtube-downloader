import React from 'react';
import { downloadStatusLabel, isProcessingDownload } from '../downloadUtils';
import './DownloadSettings.css';

export default function DownloadProgress({ media }) {
  const processing = isProcessingDownload(media);
  const progress = Number.isFinite(media.progress) ? Math.max(0, Math.min(100, Math.round(media.progress))) : 0;
  return <div className={`progress-track${processing ? ' progress-indeterminate' : ''}`} role="progressbar" aria-label={processing ? 'Video processing' : 'Download progress'} aria-valuetext={processing ? downloadStatusLabel(media) : undefined} aria-valuenow={processing ? undefined : progress} aria-valuemin={0} aria-valuemax={100}>
    <div className="progress-fill" style={processing ? undefined : { width: `${progress}%` }} />
  </div>;
}
