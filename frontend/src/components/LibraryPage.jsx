import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon";
import DownloadProgress from "./DownloadProgress";
import RetentionSettingsButton from "./RetentionSettingsButton";
import VideoRetentionDialog from "./VideoRetentionDialog";
import MediaConversionButtons from "./MediaConversionButtons";
import MediaUploadPanel from "./MediaUploadPanel";
import MediaThumbnailButton from "./MediaThumbnailButton";
import { downloadStatusLabel } from "../downloadUtils";
import VideoSearchForm, { videoSearchRoute } from "./VideoSearchForm";
import { extractUrls } from "./UrlInput";
import { deleteMedia, getMediaItems, resolveUrls, startDownloads, startMediaConversion } from "../services/api";
import { connectorBadgeClass, connectorLabel, formatDuration, formatSize, getPlayerMode, setPlayerMode, loadLikes, toggleLike } from "../mediaUtils";
const ACTIVE = new Set(["queued", "downloading", "processing"]);
const SOURCES = [{ id: "all", label: "All videos", icon: "grid" }, { id: "youtube", label: "YouTube", icon: "youtube" }, { id: "rumble", label: "Rumble", icon: "rumble" }, { id: "generic", label: "Other platforms", icon: "globe" }, { id: "upload", label: "Uploads", icon: "folder" }];
function retentionLabel(video) {
  if (!video.expires_at) return "Kept indefinitely";
  const remaining = Math.max(0, Math.ceil((new Date(video.expires_at).getTime() - Date.now()) / 86400000));
  return remaining === 0 ? "Expires today" : `Expires in ${remaining} day${remaining === 1 ? "" : "s"}`;
}
function PlayerArtwork() {
  return <div className="player-artwork" aria-hidden="true"><div className="art-orbit" /><div className="art-landscape"><div className="art-sky"><div className="art-sun" /><div className="art-mountain art-mountain-back" /><div className="art-mountain" /><span className="art-play"><Icon name="play" size={20} /></span></div><div className="art-control"><Icon name="play" size={9} /><span /><Icon name="volume" size={10} /></div><span className="art-caption">THE BIG PICTURE</span></div><div className="art-portrait"><div className="portrait-glow" /><div className="art-sun" /><div className="portrait-mountain" /><div className="portrait-mountain front" /><span className="art-play"><Icon name="play" size={17} /></span><div className="portrait-rail"><Icon name="heart" size={12} /><Icon name="download" size={12} /></div><div className="portrait-lines"><span /><span /></div></div><div className="art-switch"><Icon name="watch" size={14} /><span>↔</span><Icon name="feed" size={14} /></div><span className="art-spark spark-one">+</span><span className="art-spark spark-two">+</span></div>;
}
function MediaCard({ video, navigate, onDelete, onRetention, liked, onLike, mode, selectable, selected, onSelect, onUpdated }) {
  return <article className={`media-card${selected ? " is-selected" : ""}`}>{selectable && <label className="media-selection"><input type="checkbox" aria-label={`Select ${video.title || video.file_name || "media"}`} checked={selected} onChange={() => onSelect(video.id)} />Select</label>}<button className="media-thumb" aria-label={`Play ${video.title || video.file_name || "video"}`} onClick={() => navigate(`${mode}/${video.id}`)}>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" loading="lazy" /> : <span className={`media-thumb-fallback source-${video.connector}`}><Icon name={["youtube", "rumble"].includes(video.connector) ? video.connector : "globe"} size={42} /></span>}<span className={`${connectorBadgeClass(video.connector)} thumb-source`}>{connectorLabel(video.connector)}</span><span className="thumb-play"><Icon name="play" size={24} /></span>{formatDuration(video.duration) && <span className="thumb-duration">{formatDuration(video.duration)}</span>}</button><div className="media-card-body"><h3 title={video.title || ""}>{video.title || video.file_name || video.source_url}</h3><p>{video.uploader || connectorLabel(video.connector)}{video.file_size > 0 && <><span> · </span>{formatSize(video.file_size)}</>}<span> · </span>{retentionLabel(video)}</p><div className="media-card-actions"><button className="card-open" onClick={() => navigate(`${mode}/${video.id}`)}><Icon name={mode === "feed" ? "feed" : "watch"} size={15} />{mode === "feed" ? "Swipe feed" : "Watch"}<Icon name="arrowRight" size={14} /></button><span className="action-spacer" /><button className="icon-btn" aria-label={`Expiration settings for ${video.title || video.file_name || video.source_url}`} title="Video expiration settings" onClick={() => onRetention(video)}><Icon name="settings" size={17} /></button><button className={`icon-btn ${liked ? "is-liked" : ""}`} aria-label={liked ? "Unlike video" : "Like video"} aria-pressed={liked} title={liked ? "Unlike" : "Like"} onClick={() => onLike(video.id)}><Icon name="heart" size={17} /></button><a className="icon-btn" aria-label="Download video file" title="Download file" href={video.download_url} download><Icon name="download" size={17} /></a><button className="icon-btn danger-hover" aria-label="Delete video" title="Delete video" onClick={() => onDelete(video)}><Icon name="trash" size={16} /></button></div></div><div className="media-card-tools">{(video.media_kind === "audio" || video.playback_format === "mp3") && <span className="media-format-label">Audio with thumbnail</span>}<MediaConversionButtons video={video} formats={video.media_kind === "audio" ? ["mp4", "mp3"] : ["mp3"]} compact onMediaUpdated={onUpdated} /><MediaThumbnailButton video={video} onUpdated={onUpdated} /></div></article>;
}
export default function LibraryPage({ navigate, screen = "library", search = "", focusImport = false }) {
  const [urlText, setUrlText] = useState("");
  const urls = useMemo(() => extractUrls(urlText), [urlText]);
  const [resolved, setResolved] = useState({});
  const [resolving, setResolving] = useState(false);
  const [quality, setQuality] = useState("best");
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [source, setSource] = useState("all");
  const [sort, setSort] = useState("newest");
  const [likes, setLikes] = useState(loadLikes);
  const [mode, setMode] = useState(getPlayerMode);
  const [reload, setReload] = useState(0);
  const [retentionVideo, setRetentionVideo] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const mediaUpdated = useCallback((updated) => {
    setItems((previous) => previous?.some((item) => item.id === updated.id)
      ? previous.map((item) => item.id === updated.id ? updated : item)
      : [updated, ...(previous || [])]);
  }, []);
  const inputRef = useRef(null);
  useEffect(() => {
    if (!items) return;
    const readyIds = new Set(items.filter((item) => item.status === 'ready').map((item) => item.id));
    setSelected((previous) => new Set([...previous].filter((id) => readyIds.has(id))));
  }, [items]);
  useEffect(() => {
    if (focusImport) inputRef.current?.focus();
  }, [focusImport]);
  useEffect(() => {
    setLikes(loadLikes());
  }, [screen]);
  useEffect(() => {
    let live = true;
    let timer;
    async function load() {
      try {
        const list = await getMediaItems();
        if (live) {
          setItems(list);
          setLoadError("");
        }
      } catch (err) {
        if (live) setLoadError(err.message || "Could not connect to the video library.");
      } finally {
        if (live) timer = window.setTimeout(load, 3e3);
      }
    }
    load();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [reload]);
  useEffect(() => {
    let live = true;
    setResolved({});
    setResolving(urls.length > 0);
    if (!urls.length) return () => {
      live = false;
    };
    const timer = setTimeout(async () => {
      try {
        const results = await resolveUrls(urls);
        if (live) {
          setResolved(Object.fromEntries(results.map((item) => [item.url, item.connector])));
          setError("");
        }
      } catch (err) {
        if (live) setError(err.message);
      } finally {
        if (live) setResolving(false);
      }
    }, 300);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [urls, reload]);
  async function submit(event) {
    event.preventDefault();
    const supported = urls.filter((url) => resolved[url]);
    if (!supported.length || submitting) return;
    setSubmitting(true);
    setError("");
    setNote("");
    try {
      const started = await startDownloads(supported, quality);
      setItems((previous) => [...started, ...previous || []]);
      const remaining = urls.filter((url) => !resolved[url]);
      setUrlText(remaining.join("\n"));
      setNote(`${started.length} ${started.length === 1 ? "video added" : "videos added"} to your download queue.`);
      setReload((value) => value + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }
  async function handleDelete(video) {
    if (!window.confirm(ACTIVE.has(video.status) ? "Cancel this download and remove its files?" : "Delete this video and its files from your library?")) return;
    try {
      await deleteMedia(video.id);
      setItems((previous) => previous?.filter((item) => item.id !== video.id));
    } catch (err) {
      setError(err.message);
    }
  }
  async function retryDownload(video) {
    try {
      const started = await startDownloads([video.source_url], video.quality || "best");
      setItems((previous) => [...started, ...previous || []]);
      setNote("Retry added to your download queue.");
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }
  async function convertSelected() {
    if (batchBusy) return;
    const targets = (items || []).filter((item) => selected.has(item.id) && item.status === 'ready');
    if (!targets.length) return;
    setBatchBusy(true);
    setError('');
    setNote('');
    const failures = [];
    const accepted = [];
    // Queue requests in order; processing continues independently in the backend.
    for (const video of targets) {
      try {
        const conversion = await startMediaConversion(video.id, 'mp3');
        setItems((previous) => previous?.map((item) => item.id === video.id ? {
          ...item, conversions: { ...item.conversions, mp3: conversion },
          ...(conversion.status === 'completed' ? { playback_format: 'mp3' } : {}),
        } : item));
        accepted.push(video.id);
      } catch (err) { failures.push(`${video.title || video.file_name}: ${err.message}`); }
    }
    setSelected((previous) => new Set([...previous].filter((id) => !accepted.includes(id))));
    if (accepted.length) setNote(`${accepted.length} ${accepted.length === 1 ? 'item queued' : 'items queued'} for MP3 playback. Thumbnails stay visible while audio plays.`);
    if (failures.length) setError(failures.join(' '));
    setBatchBusy(false);
  }
  function retentionSaved(updated) {
    setItems((previous) => previous?.map((item) => item.id === updated.id ? updated : item));
    setRetentionVideo(null);
    setNote('Expiration saved for this video.');
    setReload((value) => value + 1);
  }
  const ready = (items || []).filter((item) => item.status === "ready");
  const active = (items || []).filter((item) => ACTIVE.has(item.status));
  const failed = (items || []).filter((item) => item.status === "failed");
  const filtered = ready.filter((item) => (source === "all" || (source === "generic" ? !["youtube", "rumble", "upload"].includes(item.connector) : item.connector === source)) && (screen !== "liked" || likes.has(item.id)) && [item.title, item.uploader, item.source_url].some((value) => value?.toLowerCase().includes(search.toLowerCase())));
  const visible = [...filtered].sort((a, b) => sort === "title" ? (a.title || a.source_url).localeCompare(b.title || b.source_url) : sort === "oldest" ? (a.created_at || "").localeCompare(b.created_at || "") : (b.created_at || "").localeCompare(a.created_at || ""));
  const title = screen === "liked" ? "Worth another watch" : screen === "downloads" ? "Your download queue" : "Your video library";
  const supportedCount = urls.filter((url) => resolved[url]).length;
  const selectMode = (next) => {
    setMode(next);
    setPlayerMode(next);
  };
  return <div className="page-wrap library-page"><div className="page-heading"><div><p className="eyebrow">{screen === "liked" ? "THE GOOD ONES, SAVED" : screen === "downloads" ? "FROM LINK TO LIBRARY" : "ALL YOUR FAVORITES. ONE PLACE."}</p><h1>{title}<span className="accent">.</span></h1><p className="page-description">{screen === "liked" ? "A little collection of the videos you love." : screen === "downloads" ? "Follow your downloads from start to ready-to-watch." : "Save what you love. Watch it your way."}</p></div><div className="library-total"><Icon name="library" size={18} /><strong>{ready.length}</strong><span>{ready.length === 1 ? "video" : "videos"} in your library</span></div></div>{screen === "library" && <section className="library-search" aria-labelledby="library-search-heading"><h2 id="library-search-heading">Find your next favorite</h2><p>Search all enabled websites or choose one, then add videos to your library.</p><VideoSearchForm onSearch={(query, platform) => navigate(videoSearchRoute(query, platform))} /></section>}{screen === "library" && <section className="import-panel"><div className="import-main"><span className="import-label"><span className="status-dot" /> YOUR NEXT FAVORITE STARTS HERE</span><h2>Good videos deserve<br />a place to stay.</h2><p>Paste a link from YouTube, Rumble, or another supported platform.</p><form onSubmit={submit}><div className="import-input-wrap"><Icon name="link" size={20} /><label className="sr-only" htmlFor="import-urls">Video links, one per line</label><textarea id="import-urls" ref={inputRef} rows={urls.length > 1 ? 3 : 1} placeholder="Paste a video link here…" value={urlText} onChange={(event) => {
    setUrlText(event.target.value);
    setNote("");
  }} onKeyDown={(event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit(event);
    }
  }} /><button className="btn-primary" type="submit" disabled={!supportedCount || resolving || submitting}><Icon name="download" size={17} /><span>{submitting ? "Adding\u2026" : resolving ? "Checking\u2026" : `Add video${supportedCount > 1 ? "s" : ""}`}</span></button></div><div className="import-bottom"><div className="source-legend"><span className="source-youtube"><Icon name="youtube" size={15} />YouTube</span><span className="source-rumble"><Icon name="rumble" size={15} />Rumble</span><button type="button" onClick={() => navigate("recommendations")}><Icon name="globe" size={14} />Website settings</button></div><label className="quality-label">Quality<select aria-label="Download quality" value={quality} onChange={(event) => setQuality(event.target.value)}><option value="best">Best available</option><option value="1080p">1080p</option><option value="720p">720p</option><option value="480p">480p</option></select></label></div></form>{urls.length > 0 && <div className="url-chips">{urls.map((url) => <div className="url-chip" key={url}><span title={url}>{url}</span><span className={connectorBadgeClass(resolved[url]?.id)}>{resolving ? "Checking" : resolved[url]?.name || "Unsupported"}</span></div>)}</div>}<p className="import-hint">Multiple links? Put one on each line with Shift + Enter.</p></div><PlayerArtwork /></section>}{screen !== "liked" && <MediaUploadPanel onUploaded={mediaUpdated} />}{note && <div className="success-banner" role="status"><Icon name="check" size={18} />{note}<button className="icon-btn" onClick={() => setNote("")} aria-label="Dismiss notification"><Icon name="close" size={15} /></button></div>}{(error || loadError) && <div className="error-banner" role="alert"><span>{error || `Cannot reach your library. ${loadError}`}</span><button className="link-btn" onClick={() => {
    setError("");
    setReload((value) => value + 1);
  }}>Try again</button></div>}{screen !== "liked" && (active.length > 0 || failed.length > 0 || screen === "downloads") && <section className="download-section"><div className="section-title"><h2>{screen === "downloads" ? "In progress" : "Downloads"}<span className="count-pill">{active.length}</span></h2>{active.length > 0 && <span className="muted small-text">Updates automatically</span>}</div>{active.length === 0 && failed.length === 0 && <div className="queue-empty"><span className="empty-symbol"><Icon name="download" size={25} /></span><div><h3>All caught up</h3><p>New downloads will appear here. Add a link to get started.</p></div><button className="btn-secondary" onClick={() => navigate("library/add")}>Add videos <Icon name="plus" size={16} /></button></div>}{[...active, ...failed].map((item) => <article className="download-row" key={item.id}><span className={`download-source source-${item.connector}`}><Icon name={["youtube", "rumble"].includes(item.connector) ? item.connector : "globe"} size={23} /></span><div className="download-info"><strong>{item.title || item.source_url}</strong><span>{item.status === "failed" ? item.error_message || "Download failed. Try again." : downloadStatusLabel(item)}</span>{ACTIVE.has(item.status) && <DownloadProgress media={item} />}</div>{item.status === "failed" && item.connector !== "upload" && <button className="btn-secondary" onClick={() => retryDownload(item)}><Icon name="refresh" size={15} />Retry</button>}<button className="icon-btn" aria-label={`Expiration settings for ${item.title || item.file_name || item.source_url}`} title="Video expiration settings" onClick={() => setRetentionVideo(item)}><Icon name="settings" size={17} /></button><button className="icon-btn" aria-label={item.status === "failed" ? "Dismiss failed download" : "Cancel download"} onClick={() => handleDelete(item)}><Icon name="close" size={18} /></button></article>)}</section>}<section className="collection-section"><div className="section-title"><h2>{screen === "liked" ? "Liked videos" : screen === "downloads" ? "Completed downloads" : "Your collection"}<span className="count-pill">{screen === "liked" ? ready.filter((item) => likes.has(item.id)).length : ready.length}</span></h2><div className="collection-title-actions"><RetentionSettingsButton onSaved={() => setReload((value) => value + 1)} /><div className="player-mode-switch" aria-label="Preferred player"><button className={mode === "watch" ? "selected" : ""} aria-pressed={mode === "watch"} onClick={() => selectMode("watch")}><Icon name="watch" size={15} />Watch</button><button className={mode === "feed" ? "selected" : ""} aria-pressed={mode === "feed"} onClick={() => selectMode("feed")}><Icon name="feed" size={15} />Swipe</button></div></div></div><div className="collection-toolbar"><div className="source-filters" aria-label="Filter by platform">{SOURCES.map((filter) => <button className={source === filter.id ? "selected" : ""} aria-pressed={source === filter.id} key={filter.id} onClick={() => setSource(filter.id)}><Icon name={filter.icon} size={15} />{filter.label}</button>)}</div><select className="sort-select" aria-label="Sort videos" value={sort} onChange={(event) => setSort(event.target.value)}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="title">Title A–Z</option></select></div>{screen === "downloads" && ready.length > 0 && <div className="media-batch-toolbar"><label><input type="checkbox" aria-label="Select all visible" checked={visible.length > 0 && visible.every((item) => selected.has(item.id))} disabled={batchBusy || !visible.length} onChange={(event) => setSelected((previous) => { const next = new Set(previous); visible.forEach((item) => event.target.checked ? next.add(item.id) : next.delete(item.id)); return next; })} />Select all visible</label><span>{selected.size} selected</span><button className="btn-primary" disabled={batchBusy || !selected.size} onClick={convertSelected}>{batchBusy ? "Queueing MP3 conversions…" : "Convert selected to MP3"}</button>{selected.size > 0 && <button className="link-btn" disabled={batchBusy} onClick={() => setSelected(new Set())}>Clear selection</button>}</div>}{items === null ? <div className="collection-empty"><span className="empty-symbol"><Icon name={loadError ? "refresh" : "library"} size={29} /></span><h3>{loadError ? "Your library is temporarily unavailable" : "Opening your library\u2026"}</h3><p>{loadError ? "Make sure the backend is running, then try again." : "Getting everything ready for you."}</p>{loadError && <button className="btn-secondary" onClick={() => setReload((value) => value + 1)}>Retry connection</button>}</div> : visible.length === 0 ? <div className="collection-empty"><div className="empty-card-stack"><span /><span /><span><Icon name={screen === "liked" ? "heart" : search || source !== "all" ? "search" : "play"} size={26} /></span></div><h3>{search || source !== "all" ? "No videos match just yet" : screen === "liked" ? "Keep the good ones close" : "Your collection starts with a link"}</h3><p>{search || source !== "all" ? "Try another search or choose a different platform." : screen === "liked" ? "Tap the heart on a video to find it here." : "Add your first video above. We\u2019ll take care of the rest."}</p>{!search && source === "all" && <button className="btn-secondary" onClick={() => {
    if (screen !== "library") navigate("library/add");
    else inputRef.current?.focus();
  }}><Icon name="plus" size={16} />{screen === "liked" ? "Explore your library" : "Add your first video"}</button>}<div className="empty-features"><span><Icon name="watch" size={15} />Sit back and watch</span><i /><span><Icon name="feed" size={15} />Swipe to discover</span></div></div> : <div className="media-grid">{visible.map((video) => <MediaCard key={video.id} video={video} navigate={navigate} mode={mode} selectable={screen === "downloads"} selected={selected.has(video.id)} onSelect={(id) => setSelected((previous) => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; })} onUpdated={mediaUpdated} onDelete={handleDelete} onRetention={setRetentionVideo} liked={likes.has(video.id)} onLike={(id) => setLikes((previous) => toggleLike(previous, id))} />)}</div>}</section>{retentionVideo && <VideoRetentionDialog key={retentionVideo.id} video={retentionVideo} onClose={() => setRetentionVideo(null)} onSaved={retentionSaved} />}<footer className="library-footer"><span><Icon name="folder" size={14} />Your library stays on this device.</span><span>Made for your kind of watching.</span></footer></div>;
}
