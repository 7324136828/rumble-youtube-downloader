import React, { useEffect, useRef, useState } from 'react';
import { uploadMedia } from '../services/api';
import Icon from './Icon';
import './MediaLibraryTools.css';

export default function MediaUploadPanel({ onUploaded }) {
  const [files, setFiles] = useState([]);
  const [thumbnail, setThumbnail] = useState(null);
  const [preview, setPreview] = useState('');
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const fileRef = useRef(null);
  useEffect(() => {
    if (!thumbnail) { setPreview(''); return undefined; }
    const url = URL.createObjectURL(thumbnail);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [thumbnail]);

  async function submit(event) {
    event.preventDefault();
    if (busy || !files.length) return;
    setBusy(true);
    setError('');
    const failures = [];
    const remaining = [];
    let completed = 0;
    for (const [index, file] of files.entries()) {
      setProgress(`Uploading ${index + 1} of ${files.length}: ${file.name}`);
      try {
        const media = await uploadMedia(file, thumbnail, files.length === 1 ? title : '');
        onUploaded(media);
        completed += 1;
      } catch (err) {
        failures.push(`${file.name}: ${err.message}`);
        remaining.push(file);
      }
    }
    setFiles(remaining);
    if (fileRef.current) fileRef.current.value = '';
    setError(failures.join(' '));
    setProgress(`${completed} ${completed === 1 ? 'file added' : 'files added'} to your library.${remaining.length ? ' Failed files are selected for retry.' : ''}`);
    setBusy(false);
  }

  return <section className="media-upload-panel" aria-labelledby="media-upload-heading">
    <div><h2 id="media-upload-heading"><Icon name="folder" size={20} /> Upload your own media</h2><p>Add audio or video files from your device, with optional cover art. They appear below alongside your downloads.</p></div>
    <form onSubmit={submit}>
      <label>Audio or video files<input ref={fileRef} type="file" multiple disabled={busy} onChange={(event) => { setFiles(Array.from(event.target.files || [])); setError(''); setProgress(''); }} /></label>
      <label>Title (optional)<input type="text" maxLength={500} placeholder="Uses the filename if left blank" value={title} disabled={busy || files.length > 1} onChange={(event) => setTitle(event.target.value)} /></label>
      <label>Custom thumbnail (optional)<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => setThumbnail(event.target.files?.[0] || null)} /></label>
      {preview && <img className="upload-thumbnail-preview" src={preview} alt="Custom thumbnail preview" />}
      <p className="media-upload-hint">MP3, MP4, and other audio/video formats supported by FFmpeg. A thumbnail selected here applies to all selected files.</p>
      {files.length > 0 && <p className="media-upload-files">{files.map((file) => file.name).join(', ')}</p>}
      <button className="btn-primary" type="submit" disabled={busy || !files.length}><Icon name="plus" size={17} />{busy ? 'Uploading…' : 'Upload media'}</button>
      {progress && <p role="status">{progress}</p>}
      {error && <p className="media-upload-error" role="alert">{error}</p>}
    </form>
  </section>;
}
