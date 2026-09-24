import React, { useEffect, useRef, useState } from 'react';
import { fetchWatchLaterTitle, getMediaItem, getWatchLater, getWatchLaterLinks, removeWatchLater,
  removeWatchLaterLink, previewPageLinks, saveAllPageLinks, saveWatchLater, startDownloads } from '../services/api';
import { downloadStatusLabel } from '../downloadUtils';
import { recommendationProviderLabel } from '../recommendationUtils';
import { connectorBadgeClass, setPlayerMode } from '../mediaUtils';
import { useRecommendations } from './RecommendationContext';
import VideoOrigins from './VideoOrigins';
import Icon from './Icon';
import './WatchLater.css';

const MAX_IMPORT_BYTES = 1024 * 1024;
const TITLE_POLL_MS = 2000;
const hasTitle = (item) => Boolean(item.title?.trim() && item.title.trim() !== item.source_url);
const titleActionLabel = (item, localError) => item.title_fetch_status === 'unavailable' || localError
  ? 'Retry title' : hasTitle(item) ? 'Attempt title fetch' : 'Fetch title';
const titleFailureMessage = (item, localError) => {
  const message = localError || item.title_fetch_error;
  return !message || message === 'A video title could not be found. You can enter one manually.'
    ? 'The title could not be fetched automatically. Add a title below or retry.' : message;
};

function importVideos(value) {
  const rows = Array.isArray(value) ? value : value?.videos;
  if (!Array.isArray(rows) || rows.length < 1 || rows.length > 200) throw new Error('Import an array of 1 to 200 videos, or an object with a videos array.');
  return rows.map((row) => {
    if (!row || typeof row !== 'object' || typeof row.source_url !== 'string' || !row.source_url.trim()) throw new Error('Each imported video must have a source_url.');
    const item = { source_url: row.source_url.trim() };
    for (const field of ['title', 'description']) {
      if (row[field] != null && typeof row[field] !== 'string') throw new Error(`Each ${field} must be text.`);
      if (row[field]?.trim()) item[field] = row[field].trim();
    }
    return item;
  });
}

export default function WatchLaterPage({ navigate }) {
  const { settings, reloadSettings, noteWatchLaterSaved, noteWatchLaterRemoved } = useRecommendations();
  const [source, setSource] = useState('all');
  const [refresh, setRefresh] = useState(0);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [titleRequests, setTitleRequests] = useState(() => new Set());
  const [titleErrors, setTitleErrors] = useState({});
  const [manualTitles, setManualTitles] = useState({});
  const [titleSaves, setTitleSaves] = useState(() => new Set());
  const [playback, setPlayback] = useState({});
  const [pageUrl, setPageUrl] = useState('');
  const [savedLinks, setSavedLinks] = useState([]);
  const [savedLinkTotal, setSavedLinkTotal] = useState(0);
  const [linksLoading, setLinksLoading] = useState(true);
  const [linksLoadingMore, setLinksLoadingMore] = useState(false);
  const [linksMutating, setLinksMutating] = useState(false);
  const [linksError, setLinksError] = useState('');
  const [linksNotice, setLinksNotice] = useState('');
  const [linkRefresh, setLinkRefresh] = useState(0);
  const [pagePreview, setPagePreview] = useState(null);
  const [previewView, setPreviewView] = useState('select');
  const [selectedPreviewUrls, setSelectedPreviewUrls] = useState(() => new Set());
  const [previewText, setPreviewText] = useState('');
  const [previewError, setPreviewError] = useState('');
  const [previewNotice, setPreviewNotice] = useState('');
  const [pollError, setPollError] = useState('');
  const [urls, setUrls] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [file, setFile] = useState(null);
  const fileRef = useRef(null);
  const controllerRef = useRef(null);
  const playOperations = useRef(new Map());
  const mounted = useRef(false);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const pendingMetadata = items.filter((item) => item.title_fetch_status === 'pending'
    || item.thumbnail_fetch_status === 'pending').map((item) => item.catalog_id).join(',');
  const pasted = [...new Set(urls.split(/\r?\n/).map((url) => url.trim()).filter(Boolean))];
  const providerIds = [...new Set([...settings.providers.map((provider) => provider.id), ...items.map((item) => item.connector || item.source).filter(Boolean)])];

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const operation of playOperations.current.values()) {
        operation.cancelled = true;
        clearTimeout(operation.timer);
      }
      playOperations.current.clear();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    for (const operation of playOperations.current.values()) {
      operation.cancelled = true;
      clearTimeout(operation.timer);
    }
    playOperations.current.clear();
    setPlayback({});
    setLoading(true); setLoadingMore(false); setError(''); setItems([]); setTotal(0);
    setTitleRequests(new Set()); setTitleErrors({}); setPollError('');
    getWatchLater({ source }, controller.signal).then((result) => {
      if (!controller.signal.aborted) { setItems(result.items || []); setTotal(result.total || 0); noteWatchLaterSaved((result.items || []).map((item) => item.source_url)); }
    }).catch((err) => {
      if (!controller.signal.aborted) setError(err.message || 'Could not load Watch later.');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [source, refresh, noteWatchLaterSaved]);

  useEffect(() => {
    const controller = new AbortController();
    setLinksLoading(true); setLinksLoadingMore(false); setLinksError('');
    getWatchLaterLinks({}, controller.signal).then((result) => {
      if (!controller.signal.aborted) { setSavedLinks(result.items || []); setSavedLinkTotal(result.total || 0); }
    }).catch((err) => {
      if (!controller.signal.aborted) setLinksError(err.message || 'Could not load saved page links.');
    }).finally(() => { if (!controller.signal.aborted) setLinksLoading(false); });
    return () => controller.abort();
  }, [linkRefresh]);

  useEffect(() => {
    if (!pagePreview) return undefined;
    const close = (event) => {
      if (event.key === 'Escape' && !linksMutating) setPagePreview(null);
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [pagePreview, linksMutating]);

  useEffect(() => {
    if (!pendingMetadata || loading || loadingMore || mutating || titleRequests.size) return undefined;
    const controller = new AbortController();
    let timer;
    const poll = async () => {
      if (document.hidden) { timer = setTimeout(poll, TITLE_POLL_MS); return; }
      try {
        // Only refresh already loaded rows. Extra pages stay visible while titles resolve.
        const count = itemsRef.current.length;
        const pages = await Promise.all(Array.from({ length: Math.ceil(count / 200) }, (_, page) =>
          getWatchLater({ source, offset: page * 200, limit: Math.min(200, count - page * 200) }, controller.signal)));
        if (controller.signal.aborted) return;
        const updates = new Map(pages.flatMap((page) => page.items || []).map((item) => [item.catalog_id, item]));
        const previous = itemsRef.current;
        const changedTitle = previous.some((item) => {
          const updated = updates.get(item.catalog_id);
          return updated && updated.title !== item.title && hasTitle(updated);
        });
        const updatedItems = previous.filter((item) => updates.has(item.catalog_id)).map((item) => updates.get(item.catalog_id));
        setItems(updatedItems);
        setTotal(pages[0]?.total || 0);
        setPollError('');
        if (changedTitle) reloadSettings();
        if (updatedItems.some((item) => item.title_fetch_status === 'pending'
          || item.thumbnail_fetch_status === 'pending')) timer = setTimeout(poll, TITLE_POLL_MS);
      } catch (err) {
        if (!controller.signal.aborted) {
          setPollError('Could not refresh video titles. Retrying shortly.');
          timer = setTimeout(poll, TITLE_POLL_MS);
        }
      }
    };
    timer = setTimeout(poll, TITLE_POLL_MS);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [source, refresh, pendingMetadata, items.length, loading, loadingMore, mutating, titleRequests.size, reloadSettings]);

  const loadMore = async () => {
    const controller = controllerRef.current;
    if (!controller || controller.signal.aborted || loadingMore) return;
    setLoadingMore(true); setError('');
    try {
      const result = await getWatchLater({ source, offset: items.length }, controller.signal);
      if (!controller.signal.aborted) {
        setItems((previous) => [...new Map([...previous, ...(result.items || [])].map((item) => [item.catalog_id, item])).values()]);
        setTotal(result.total || 0);
        noteWatchLaterSaved((result.items || []).map((item) => item.source_url));
      }
    } catch (err) { if (!controller.signal.aborted) setError(err.message || 'Could not load more saved videos.'); }
    finally { if (!controller.signal.aborted) setLoadingMore(false); }
  };
  const save = async (videos) => {
    if (!videos.length || videos.length > 200) { setError('Add between 1 and 200 video links at a time.'); return false; }
    setMutating(true); setError(''); setNotice('');
    try {
      const result = await saveWatchLater(videos);
      if (mounted.current) {
        setNotice(`${result.added || 0} video${result.added === 1 ? '' : 's'} added${result.updated ? `, ${result.updated} updated` : ''} to Watch later.`);
        setRefresh((value) => value + 1);
        noteWatchLaterSaved((result.items || videos).map((item) => item.source_url));
        reloadSettings();
      }
      return true;
    } catch (err) { if (mounted.current) setError(err.message || 'Could not save these videos.'); return false; }
    finally { if (mounted.current) setMutating(false); }
  };
  const add = async (event) => {
    event.preventDefault();
    const videos = pasted.map((source_url) => ({ source_url, ...(pasted.length === 1 && title.trim() ? { title: title.trim() } : {}), ...(pasted.length === 1 && description.trim() ? { description: description.trim() } : {}) }));
    if (await save(videos)) { setUrls(''); setTitle(''); setDescription(''); }
  };
  const upload = async (event) => {
    event.preventDefault();
    if (!file) return;
    setError(''); setNotice('');
    if (file.size > MAX_IMPORT_BYTES) { setError('Choose a JSON file no larger than 1 MiB.'); return; }
    setMutating(true);
    try {
      let parsed;
      try { parsed = JSON.parse((await file.text()).replace(/^\uFEFF/, '')); }
      catch { throw new Error('Choose a valid JSON video list.'); }
      if (await save(importVideos(parsed))) { setFile(null); if (fileRef.current) fileRef.current.value = ''; }
    } catch (err) { if (mounted.current) setError(err.message); }
    finally { if (mounted.current) setMutating(false); }
  };
  const savePageLinks = async (event) => {
    event.preventDefault();
    const value = pageUrl.trim();
    if (!value || linksMutating) return;
    setLinksMutating(true); setLinksError(''); setLinksNotice('');
    try {
      const result = await previewPageLinks(value);
      if (mounted.current) {
        const videoUrls = [...new Set((result.items || []).filter((item) => item.is_video && item.video_url)
          .map((item) => item.video_url))];
        setPagePreview(result); setPreviewView('select'); setSelectedPreviewUrls(new Set(videoUrls));
        setPreviewText(videoUrls.join('\n')); setPreviewError(''); setPreviewNotice('');
      }
    } catch (err) { if (mounted.current) setLinksError(err.message || 'Could not read links from this page.'); }
    finally { if (mounted.current) setLinksMutating(false); }
  };
  const previewVideos = () => {
    const urls = previewView === 'select'
      ? [...selectedPreviewUrls]
      : [...new Set(previewText.split(/\r?\n/).map((url) => url.trim()).filter(Boolean))];
    const metadata = new Map();
    for (const item of pagePreview?.items || []) {
      metadata.set(item.url, item);
      if (item.video_url) metadata.set(item.video_url, item);
    }
    return urls.map((source_url) => {
      const titleValue = metadata.get(source_url)?.title?.trim();
      return { source_url, ...(titleValue ? { title: titleValue } : {}) };
    });
  };
  const addPreviewVideos = async () => {
    const videos = previewVideos();
    if (!videos.length || videos.length > 200) {
      setPreviewError(videos.length ? 'Add no more than 200 video URLs at a time.' : 'Select or paste at least one video URL.');
      return;
    }
    setLinksMutating(true); setPreviewError(''); setPreviewNotice('');
    try {
      const result = await saveWatchLater(videos);
      if (mounted.current) {
        setNotice(`${result.added || 0} video${result.added === 1 ? '' : 's'} added${result.updated ? `, ${result.updated} updated` : ''} to Watch later.`);
        setPageUrl(''); setPagePreview(null); setRefresh((value) => value + 1);
        noteWatchLaterSaved((result.items || videos).map((item) => item.source_url));
        reloadSettings();
      }
    } catch (err) { if (mounted.current) setPreviewError(err.message || 'Could not add these videos.'); }
    finally { if (mounted.current) setLinksMutating(false); }
  };
  const archivePreviewLinks = async () => {
    if (!pagePreview || linksMutating) return;
    setLinksMutating(true); setPreviewError(''); setPreviewNotice('');
    try {
      const result = await saveAllPageLinks(pagePreview.page_url);
      if (mounted.current) {
        setPreviewNotice(`${result.added || 0} page link${result.added === 1 ? '' : 's'} archived${result.updated ? `, ${result.updated} updated` : ''}.`);
        setLinkRefresh((current) => current + 1);
      }
    } catch (err) { if (mounted.current) setPreviewError(err.message || 'Could not archive these page links.'); }
    finally { if (mounted.current) setLinksMutating(false); }
  };
  const copyPreviewLinks = async () => {
    try {
      await navigator.clipboard.writeText((pagePreview?.items || []).map((item) => item.url).join('\n'));
      setPreviewNotice('All page links copied. Edit them anywhere, then paste the video URLs below.');
    } catch {
      setPreviewError('Could not copy automatically. Select and copy the All page links text manually.');
    }
  };
  const removePageLink = async (item) => {
    if (linksMutating) return;
    setLinksMutating(true); setLinksError(''); setLinksNotice('');
    try {
      await removeWatchLaterLink(item.id);
      if (mounted.current) { setLinksNotice('Saved link removed.'); setLinkRefresh((current) => current + 1); }
    } catch (err) { if (mounted.current) setLinksError(err.message || 'Could not remove this saved link.'); }
    finally { if (mounted.current) setLinksMutating(false); }
  };
  const loadMorePageLinks = async () => {
    if (linksLoadingMore || linksMutating) return;
    setLinksLoadingMore(true); setLinksError('');
    try {
      const result = await getWatchLaterLinks({ offset: savedLinks.length });
      if (mounted.current) {
        setSavedLinks((previous) => [...new Map([...previous, ...(result.items || [])].map((item) => [item.id, item])).values()]);
        setSavedLinkTotal(result.total || 0);
      }
    } catch (err) { if (mounted.current) setLinksError(err.message || 'Could not load more saved links.'); }
    finally { if (mounted.current) setLinksLoadingMore(false); }
  };
  const remove = async (item) => {
    setMutating(true); setError(''); setNotice('');
    try {
      await removeWatchLater(item.catalog_id);
      if (mounted.current) { setNotice('Video removed from Watch later.'); noteWatchLaterRemoved(item.source_url); setRefresh((value) => value + 1); reloadSettings(); }
    } catch (err) { if (mounted.current) setError(err.message || 'Could not remove this saved video.'); }
    finally { if (mounted.current) setMutating(false); }
  };
  const fetchTitle = async (item) => {
    const controller = controllerRef.current;
    if (!controller || controller.signal.aborted || titleRequests.has(item.catalog_id)) return;
    setTitleRequests((previous) => new Set([...previous, item.catalog_id]));
    setTitleErrors((previous) => ({ ...previous, [item.catalog_id]: '' }));
    try {
      const result = await fetchWatchLaterTitle(item.catalog_id, controller.signal, { force: hasTitle(item) });
      if (!controller.signal.aborted && result.item) {
        setItems((previous) => previous.map((entry) => entry.catalog_id === item.catalog_id ? result.item : entry));
        if (result.item.title !== item.title && hasTitle(result.item)) reloadSettings();
      }
    } catch (err) {
      if (!controller.signal.aborted) setTitleErrors((previous) => ({ ...previous, [item.catalog_id]: err.message || 'Could not fetch this video title.' }));
    } finally {
      if (!controller.signal.aborted) setTitleRequests((previous) => { const next = new Set(previous); next.delete(item.catalog_id); return next; });
    }
  };
  const saveManualTitle = async (event, item) => {
    event.preventDefault();
    const value = (manualTitles[item.catalog_id] || '').trim();
    if (!value || titleSaves.has(item.catalog_id)) return;
    setTitleSaves((previous) => new Set([...previous, item.catalog_id]));
    setTitleErrors((previous) => ({ ...previous, [item.catalog_id]: '' }));
    try {
      const result = await saveWatchLater([{ source_url: item.source_url, title: value }], { fetchTitles: false, resolveRedirects: false });
      const saved = result.items?.[0];
      if (mounted.current && saved) {
        setItems((previous) => previous.map((entry) => entry.catalog_id === item.catalog_id ? saved : entry));
        setManualTitles((previous) => ({ ...previous, [item.catalog_id]: '' }));
        reloadSettings();
      }
    } catch (err) {
      if (mounted.current) setTitleErrors((previous) => ({ ...previous, [item.catalog_id]: err.message || 'Could not save this title.' }));
    } finally {
      if (mounted.current) setTitleSaves((previous) => { const next = new Set(previous); next.delete(item.catalog_id); return next; });
    }
  };
  const openMedia = (id) => {
    setPlayerMode('watch');
    if (navigate) navigate(`watch/${id}`);
    else window.location.hash = `watch/${id}`;
  };
  const play = async (item) => {
    if (item.media_id) { openMedia(item.media_id); return; }
    if (playOperations.current.has(item.catalog_id)) return;
    const operation = { cancelled: false, timer: null, polls: 0 };
    playOperations.current.set(item.catalog_id, operation);
    const valid = () => mounted.current && !operation.cancelled && playOperations.current.get(item.catalog_id) === operation;
    const update = (value) => { if (valid()) setPlayback((previous) => ({ ...previous, [item.catalog_id]: value })); };
    const finish = () => playOperations.current.delete(item.catalog_id);
    const fail = (message) => {
      finish();
      if (mounted.current && !operation.cancelled) setPlayback((previous) => ({ ...previous, [item.catalog_id]: { status: 'failed', error: message } }));
    };
    const ready = (media) => {
      finish();
      if (mounted.current && !operation.cancelled) openMedia(media.id);
    };
    const poll = async (id) => {
      if (!valid()) return;
      try {
        const media = await getMediaItem(id);
        if (!valid()) return;
        if (media.status === 'ready') { ready(media); return; }
        if (media.status === 'failed') { fail(media.error_message || media.error || 'This video could not be prepared for playback.'); return; }
        update({ status: 'downloading', mediaStatus: media.status, progress: media.progress, stage: media.stage });
        if (++operation.polls >= 600) { fail('This video is still being prepared. Check Downloads for its progress.'); return; }
        operation.timer = setTimeout(() => poll(id), 2000);
      } catch (err) { fail(err.message || 'Could not check this download.'); }
    };
    update({ status: 'starting' });
    try {
      const media = (await startDownloads([item.source_url], 'best'))?.[0];
      if (!valid()) return;
      if (!media?.id) throw new Error('The video could not be added to Downloads.');
      if (media.status === 'ready') ready(media);
      else await poll(media.id);
    } catch (err) { fail(err.message || 'Could not prepare this video for playback.'); }
  };

  return <div className="page-wrap watch-later-page">
    <div className="page-heading"><div><p className="eyebrow">YOUR OWN VIDEO PICKS</p><h1>Watch later<span className="accent">.</span></h1><p className="page-description">Save links for another day and give recommendations your own collection to choose from.</p></div></div>
    {error && <div className="error-banner" role="alert"><span>{error}</span><button className="link-btn" disabled={mutating} onClick={() => setRefresh((value) => value + 1)}>Reload saved videos</button></div>}
    {notice && <div className="success-banner" role="status">{notice}</div>}
    <div className="watch-later-forms">
      <section className="recommendation-settings-card"><h2>Add video links</h2><form onSubmit={add}>
        <label className="recommendation-field" htmlFor="watch-later-urls">Video URLs<textarea id="watch-later-urls" value={urls} onChange={(event) => setUrls(event.target.value)} placeholder="One video link per line" rows={3} disabled={mutating} required /></label>
        <label className="recommendation-field" htmlFor="watch-later-title">Title (optional, one video)<input id="watch-later-title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={500} disabled={mutating || pasted.length > 1} /></label>
        <label className="recommendation-field" htmlFor="watch-later-description">Description (optional, one video)<textarea id="watch-later-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} rows={2} disabled={mutating || pasted.length > 1} /></label>
        <p className="recommendation-help">Leave the title blank to fetch it in the background. This needs no downloads or AI model. Add up to 200 individual video links from your configured websites. Saved links can be recommended even with unverified discoveries off, but keep their Unverified label until confirmed.</p>
        <button className="btn-primary" disabled={mutating || !pasted.length}><Icon name="plus" size={15} />{mutating ? 'Saving…' : 'Save for later'}</button>
      </form></section>
      <section className="recommendation-settings-card"><h2>Import a video list</h2><form onSubmit={upload}>
        <label className="recommendation-field" htmlFor="watch-later-import">JSON file<input id="watch-later-import" ref={fileRef} type="file" accept=".json,application/json" disabled={mutating} onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
        <p className="recommendation-help">Import an array of videos or an object with a <code>videos</code> array. Each entry needs <code>source_url</code> and may include <code>title</code> and <code>description</code>. Missing titles are fetched in the background; supplied titles stay as entered. Up to 200 videos and 1 MiB per file.</p>
        <button className="btn-secondary" disabled={!file || mutating}><Icon name="folder" size={15} />Import video list</button>
      </form></section>
      <section className="recommendation-settings-card"><h2>Save every link from a page</h2><form onSubmit={savePageLinks}>
        <label className="recommendation-field" htmlFor="watch-later-page-url">Public page URL<input id="watch-later-page-url" value={pageUrl} onChange={(event) => setPageUrl(event.target.value)} placeholder="https://example.com/page" maxLength={2048} disabled={linksMutating} required /></label>
        <p className="recommendation-help">Fetch up to 500 links into a review window. Select recognized videos or switch to an editable text list, then add up to 200 videos to Watch later in one batch.</p>
        <button className="btn-primary" disabled={linksMutating || !pageUrl.trim()}><Icon name="bolt" size={15} />{linksMutating ? 'Reading links…' : 'Save all links'}</button>
      </form></section>
    </div>
    <div className="watch-later-toolbar"><h2>Saved videos <span className="count-pill">{total}</span></h2><label className="recommendation-field">Website<select aria-label="Watch later website" value={source} onChange={(event) => setSource(event.target.value)} disabled={mutating}><option value="all">All websites</option>{providerIds.map((id) => <option key={id} value={id}>{recommendationProviderLabel(id, settings.providers)}</option>)}</select></label></div>
    {pollError && <p className="watch-later-poll-error" role="status">{pollError}</p>}
    {loading ? <p className="muted" role="status">Loading saved videos…</p> : items.length ? <div className="watch-later-list">{items.map((item) => <article className="watch-later-item" key={item.catalog_id}>
      <div className="watch-later-item-thumbnail"><span><Icon name="play" size={24} /></span>{item.thumbnail_url && <img src={item.thumbnail_url} alt="" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; }} />}</div>
      <div className="watch-later-item-body"><span className={connectorBadgeClass(item.connector || item.source)}>{recommendationProviderLabel(item.connector || item.source, settings.providers)}</span> <VideoOrigins video={{ ...item, user_added: true }} />{item.verified === false && <span className="recommendation-unverified">Unverified link</span>}{item.title_fetch_method === 'ai' && <span className="recommendation-unverified">AI-generated title</span>}<h3>{item.title || item.source_url}</h3>{item.description && <p>{item.description}</p>}
        {item.thumbnail_fetch_status === 'pending' && <p className="watch-later-thumbnail-status" role="status">Downloading thumbnail…</p>}
        {(item.title_fetch_status === 'pending' || titleRequests.has(item.catalog_id)) && <p className="watch-later-title-status" role="status">Fetching title…</p>}
        {(titleErrors[item.catalog_id] || item.title_fetch_status === 'unavailable') && <><p className="watch-later-title-error" role="status">{titleFailureMessage(item, titleErrors[item.catalog_id])}</p><form className="watch-later-manual-title" onSubmit={(event) => saveManualTitle(event, item)}><label className="sr-only" htmlFor={`watch-later-manual-title-${item.catalog_id}`}>Title for {item.source_url}</label><input id={`watch-later-manual-title-${item.catalog_id}`} value={manualTitles[item.catalog_id] || ''} onChange={(event) => setManualTitles((previous) => ({ ...previous, [item.catalog_id]: event.target.value }))} maxLength={500} placeholder="Enter a title" disabled={titleSaves.has(item.catalog_id)} /><button className="btn-secondary" disabled={titleSaves.has(item.catalog_id) || !(manualTitles[item.catalog_id] || '').trim()}>{titleSaves.has(item.catalog_id) ? 'Saving…' : 'Save title'}</button></form></>}
        {playback[item.catalog_id]?.error && <p className="watch-later-play-error" role="alert">{playback[item.catalog_id].error}</p>}
      </div>
      <div className="watch-later-item-actions"><button className="btn-primary" aria-label={`Play saved video: ${item.title || item.source_url}`} disabled={['starting', 'downloading'].includes(playback[item.catalog_id]?.status)} onClick={() => play(item)}><Icon name="play" size={14} />{playback[item.catalog_id]?.status === 'starting' ? 'Preparing…' : playback[item.catalog_id]?.status === 'downloading' ? downloadStatusLabel({ ...playback[item.catalog_id], status: playback[item.catalog_id].mediaStatus }) : playback[item.catalog_id]?.error ? 'Retry play' : 'Play'}</button><button className="btn-secondary" aria-label={`${titleActionLabel(item, titleErrors[item.catalog_id])}: ${item.source_url}`} disabled={mutating || item.title_fetch_status === 'pending' || titleRequests.has(item.catalog_id)} onClick={() => fetchTitle(item)}>{item.title_fetch_status === 'pending' || titleRequests.has(item.catalog_id) ? 'Fetching title…' : titleActionLabel(item, titleErrors[item.catalog_id])}</button><a className="btn-secondary" href={item.source_url} target="_blank" rel="noopener noreferrer"><Icon name="external" size={14} />Open original</a><button className="btn-secondary" aria-label={`Remove saved video: ${item.title || item.source_url}`} disabled={mutating} onClick={() => remove(item)}><Icon name="trash" size={14} />Remove</button></div>
    </article>)}</div> : !error && <div className="collection-empty"><Icon name="folder" size={28} /><h3>No saved videos here yet</h3><p>Paste video links above or use Watch later on a search result.</p></div>}
    {!loading && items.length < total && <button className="btn-secondary watch-later-load-more" disabled={loadingMore || mutating} onClick={loadMore}>{loadingMore ? 'Loading…' : 'Load more saved videos'}</button>}
    <section className="watch-later-links" aria-labelledby="saved-page-links-heading">
      <div className="watch-later-toolbar"><h2 id="saved-page-links-heading">Saved page links <span className="count-pill">{savedLinkTotal}</span></h2></div>
      {linksError && <div className="error-banner" role="alert"><span>{linksError}</span><button className="link-btn" disabled={linksMutating} onClick={() => setLinkRefresh((current) => current + 1)}>Reload links</button></div>}
      {linksNotice && <div className="success-banner" role="status">{linksNotice}</div>}
      {linksLoading ? <p className="muted" role="status">Loading saved links…</p> : savedLinks.length ? <div className="watch-later-link-list">{savedLinks.map((item) => <article className="watch-later-link-item" key={item.id}><div><h3>{item.title || item.url}</h3><p>{item.url}</p><span>Found on {item.source_page}</span></div><div className="watch-later-item-actions"><a className="btn-primary" href={item.url} target="_blank" rel="noopener noreferrer"><Icon name="external" size={14} />Open link</a><button className="btn-secondary" aria-label={`Remove saved link: ${item.title || item.url}`} disabled={linksMutating} onClick={() => removePageLink(item)}><Icon name="trash" size={14} />Remove</button></div></article>)}</div> : !linksError && <p className="muted">No page links saved yet.</p>}
      {!linksLoading && savedLinks.length < savedLinkTotal && <button className="btn-secondary watch-later-load-more" disabled={linksLoadingMore || linksMutating} onClick={loadMorePageLinks}>{linksLoadingMore ? 'Loading…' : 'Load more saved links'}</button>}
    </section>
    {pagePreview && <div className="link-picker-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !linksMutating) setPagePreview(null); }}>
      <section className="link-picker-modal" role="dialog" aria-modal="true" aria-labelledby="link-picker-heading">
        <header><div><p className="eyebrow">PAGE LINK REVIEW</p><h2 id="link-picker-heading">Choose videos to save</h2><p>{pagePreview.total} links found · {pagePreview.video_total} recognized videos</p></div><button className="link-picker-close" aria-label="Close link review" disabled={linksMutating} onClick={() => setPagePreview(null)}>×</button></header>
        <div className="link-picker-tabs" role="tablist" aria-label="Link review view">
          <button role="tab" aria-selected={previewView === 'select'} className={previewView === 'select' ? 'active' : ''} onClick={() => setPreviewView('select')}>Select videos</button>
          <button role="tab" aria-selected={previewView === 'text'} className={previewView === 'text' ? 'active' : ''} onClick={() => setPreviewView('text')}>Text view</button>
        </div>
        {previewView === 'select' ? <div className="link-picker-select-view">
          <div className="link-picker-tools"><span>{selectedPreviewUrls.size} selected</span><button className="link-btn" onClick={() => setSelectedPreviewUrls(new Set((pagePreview.items || []).filter((item) => item.is_video && item.video_url).map((item) => item.video_url)))}>Select all videos</button><button className="link-btn" onClick={() => setSelectedPreviewUrls(new Set())}>Clear</button></div>
          <div className="link-picker-list">{(pagePreview.items || []).map((item, index) => <label className={`link-picker-row${item.is_video ? '' : ' unavailable'}`} key={`${item.url}-${index}`}>
            <input type="checkbox" disabled={!item.is_video || linksMutating} checked={Boolean(item.video_url && selectedPreviewUrls.has(item.video_url))} onChange={(event) => setSelectedPreviewUrls((previous) => { const next = new Set(previous); if (event.target.checked) next.add(item.video_url); else next.delete(item.video_url); return next; })} />
            <span><strong>{item.title || item.url}</strong><code>{item.url}</code>{item.is_video ? <small>{recommendationProviderLabel(item.provider, settings.providers)} · Thumbnail downloads after saving</small> : <small>Not recognized as a configured video</small>}</span>
            <a href={item.url} target="_blank" rel="noopener noreferrer" onClick={(event) => event.stopPropagation()}>Open</a>
          </label>)}</div>
        </div> : <div className="link-picker-text-view">
          <p>Keep one video URL per line. You can copy every page link, edit the list in your own editor, and paste the final video URLs back here.</p>
          <label htmlFor="link-picker-all-text">All page links<textarea id="link-picker-all-text" value={(pagePreview.items || []).map((item) => item.url).join('\n')} rows={8} readOnly /></label>
          <button className="btn-secondary" type="button" onClick={copyPreviewLinks}><Icon name="copy" size={14} />Copy all page links</button>
          <label htmlFor="link-picker-text">Video URLs to add<textarea id="link-picker-text" value={previewText} onChange={(event) => setPreviewText(event.target.value)} rows={12} disabled={linksMutating} placeholder="One video URL per line" /></label>
        </div>}
        {previewError && <div className="error-banner" role="alert">{previewError}</div>}
        {previewNotice && <div className="success-banner" role="status">{previewNotice}</div>}
        <footer><button className="btn-secondary" disabled={linksMutating} onClick={archivePreviewLinks}>Archive all page links</button><span /><button className="btn-secondary" disabled={linksMutating} onClick={() => setPagePreview(null)}>Cancel</button><button className="btn-primary" disabled={linksMutating || previewVideos().length < 1} onClick={addPreviewVideos}>{linksMutating ? 'Adding…' : `Add ${previewVideos().length} video${previewVideos().length === 1 ? '' : 's'}`}</button></footer>
      </section>
    </div>}
  </div>;
}
