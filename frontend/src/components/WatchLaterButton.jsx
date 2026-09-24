import React, { useEffect, useState } from 'react';
import { saveWatchLater } from '../services/api';
import { useRecommendations } from './RecommendationContext';
import Icon from './Icon';
import './WatchLater.css';

export default function WatchLaterButton({ video }) {
  const { reloadSettings, savedWatchLaterUrls, noteWatchLaterSaved } = useRecommendations();
  const initiallySaved = Boolean(video.user_added || video.origins?.includes('watch_later') || savedWatchLaterUrls.has(video.source_url));
  const [saved, setSaved] = useState(initiallySaved);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setSaved(initiallySaved); setError(''); }, [video.source_url, initiallySaved]);
  const save = async () => {
    if (saving || saved) return;
    setSaving(true);
    setError('');
    try {
      await saveWatchLater([{ source_url: video.source_url, ...(video.title ? { title: video.title } : {}), ...(video.description ? { description: video.description } : {}) }]);
      setSaved(true);
      noteWatchLaterSaved([video.source_url]);
      reloadSettings();
    } catch (err) { setError(err.message || 'Could not save this video for later.'); }
    finally { setSaving(false); }
  };
  return <div className="watch-later-action"><button type="button" className="btn-secondary" aria-label={`${saved ? 'Saved for later' : 'Watch later'}: ${video.title || video.source_url}`} disabled={saving || saved} onClick={save}><Icon name={saved ? 'check' : 'folder'} size={14} />{saved ? 'Saved for later' : saving ? 'Saving…' : 'Watch later'}</button>{error && <p className="watch-later-action-error" role="alert">{error}</p>}</div>;
}
