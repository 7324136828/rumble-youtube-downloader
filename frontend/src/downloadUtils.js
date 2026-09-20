const PROCESSING_STAGES = {
  checking: 'Checking browser compatibility',
  converting: 'Converting for browser playback',
  thumbnail: 'Generating thumbnail',
  merging: 'Combining video and audio',
  processing: 'Preparing video for playback',
};

export function isProcessingDownload(media) {
  return media?.status === 'processing' || Object.hasOwn(PROCESSING_STAGES, media?.stage || '');
}

export function downloadStatusLabel(media) {
  if (isProcessingDownload(media)) return PROCESSING_STAGES[media.stage] || PROCESSING_STAGES.processing;
  const label = media?.status === 'queued' ? 'Queued' : 'Downloading';
  const progress = Number.isFinite(media?.progress) ? Math.max(0, Math.min(100, Math.round(media.progress))) : 0;
  return `${label} · ${progress}%`;
}
