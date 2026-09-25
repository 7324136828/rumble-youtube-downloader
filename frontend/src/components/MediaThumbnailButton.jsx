import React, { useState } from 'react';
import { uploadMediaThumbnail } from '../services/api';
import './MediaLibraryTools.css';

export default function MediaThumbnailButton({ video, onUpdated }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function change(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || busy) return;
    setBusy(true);
    setError('');
    try { onUpdated(await uploadMediaThumbnail(video.id, file)); }
    catch (err) { setError(err.message || 'Could not update thumbnail.'); }
    finally { setBusy(false); }
  }
  return <div className="media-thumbnail-action"><label className={`thumbnail-upload-button${busy ? ' is-disabled' : ''}`}>
    {busy ? 'Saving thumbnail…' : 'Custom thumbnail'}
    <input className="sr-only" type="file" accept="image/jpeg,image/png,image/webp" aria-label={`Custom thumbnail for ${video.title || video.file_name || 'media'}`} disabled={busy} onChange={change} />
  </label>{error && <span role="alert" className="media-upload-error">{error}</span>}</div>;
}
