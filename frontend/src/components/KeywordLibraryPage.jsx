import React, { useEffect, useState } from 'react';
import Icon from './Icon';
import { useRecommendations } from './RecommendationContext';
import { connectorLabel, formatDuration } from '../mediaUtils';
import { getVideoKeywords } from '../services/api';

const EMPTY = { query: '', keywords: [], videos: [], status: {} };
const KEYWORD_PAGE_SIZE = 30;

export default function KeywordLibraryPage({ navigate }) {
  const { settings } = useRecommendations();
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [visibleKeywordCount, setVisibleKeywordCount] = useState(KEYWORD_PAGE_SIZE);

  useEffect(() => {
    const controller = new AbortController();
    const searchSession = query ? (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`) : '';
    let timer;
    const load = async () => {
      try {
        const result = await getVideoKeywords(query, controller.signal, searchSession);
        if (controller.signal.aborted) return;
        setData(result || EMPTY);
        setError('');
        if ((result?.status?.pending || 0) > 0) timer = window.setTimeout(load, 2000);
      } catch (err) {
        if (!controller.signal.aborted && err.name !== 'AbortError') setError(err.message || 'Could not load video keywords.');
      }
    };
    load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [query]);

  const selectKeyword = (keyword) => {
    setDraft(keyword);
    setQuery(keyword);
  };
  const submit = (event) => {
    event.preventDefault();
    setQuery(draft.trim());
  };
  const clear = () => {
    setDraft('');
    setQuery('');
  };
  const maxCount = Math.max(1, ...(data?.keywords || []).map((item) => item.count));
  const visibleKeywords = (data?.keywords || []).slice(0, visibleKeywordCount);
  const hiddenKeywordCount = Math.max(0, (data?.keywords?.length || 0) - visibleKeywordCount);

  return <div className="page-wrap keyword-library-page">
    <div className="page-heading"><div><p className="eyebrow">EXPLORE YOUR COLLECTION</p><h1>Video topics<span className="accent">.</span></h1><p className="page-description">Search your saved videos using AI-generated keywords.</p></div><span className="soft-pill"><Icon name="bolt" size={15} />AI organized</span></div>
    <form className="keyword-search" role="search" onSubmit={submit}>
      <Icon name="search" size={19} />
      <input type="search" aria-label="Search videos by keyword" placeholder="Search a topic…" value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={100} />
      {draft && <button type="button" className="icon-btn" aria-label="Clear keyword search" onClick={clear}><Icon name="close" size={14} /></button>}
      <button className="btn-primary" type="submit">Search</button>
    </form>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {!settings.enabled && <div className="keyword-note" role="status"><Icon name="info" size={18} /><span>Turn on Recommendations to generate keywords for downloaded videos. Existing keywords remain searchable.</span></div>}
    {(data?.status?.pending || 0) > 0 && <p className="keyword-progress" role="status"><span className="status-dot" />Organizing {data.status.pending} video{data.status.pending === 1 ? '' : 's'}…</p>}
    <section className="keyword-cloud-section" aria-labelledby="keyword-cloud-heading">
      <div className="section-title"><h2 id="keyword-cloud-heading">Word cloud<span className="count-pill">{data?.keywords?.length || 0}</span></h2>{query && <button className="link-btn" onClick={clear}>Show all topics</button>}</div>
      {data === null ? <div className="collection-empty" role="status"><Icon name="bolt" size={28} /><h3>Opening your topics…</h3></div> : data.keywords.length ? <><div className="keyword-cloud" id="keyword-cloud">{visibleKeywords.map((item) => <button type="button" key={item.keyword} aria-pressed={query.toLowerCase() === item.keyword.toLowerCase()} onClick={() => selectKeyword(item.keyword)} style={{ fontSize: `${11 + (item.count / maxCount) * 14}px` }}><span>{item.keyword}</span><small>{item.count}</small></button>)}</div>{hiddenKeywordCount > 0 && <div className="keyword-cloud-more"><button type="button" className="btn-secondary" aria-controls="keyword-cloud" onClick={() => setVisibleKeywordCount((count) => count + KEYWORD_PAGE_SIZE)}>Add more <span>({Math.min(KEYWORD_PAGE_SIZE, hiddenKeywordCount)})</span></button></div>}</> : <div className="collection-empty"><Icon name="search" size={28} /><h3>No video topics yet</h3><p>{settings.enabled ? 'Keywords appear here after AI finishes organizing videos with a title or description.' : 'Enable Recommendations, then add or download a video to build your word cloud.'}</p></div>}
    </section>
    {data && data.keywords.length > 0 && <section className="keyword-results" aria-labelledby="keyword-results-heading">
      <div className="section-title"><h2 id="keyword-results-heading">{query ? `Videos tagged “${query}”` : 'Keyword-tagged videos'}<span className="count-pill">{data.videos.length}</span></h2></div>
      {data.videos.length ? <div className="media-grid">{data.videos.map((video) => <article className="media-card keyword-video-card" key={video.id}>
        <button className="media-thumb" aria-label={`Play ${video.title || 'video'}`} onClick={() => navigate(`watch/${video.id}`)}>{video.thumbnail_url ? <img src={video.thumbnail_url} alt="" loading="lazy" /> : <span className={`media-thumb-fallback source-${video.connector}`}><Icon name={video.connector === 'generic' ? 'globe' : video.connector} size={40} /></span>}<span className="thumb-play"><Icon name="play" size={24} /></span>{formatDuration(video.duration) && <span className="thumb-duration">{formatDuration(video.duration)}</span>}</button>
        <div className="media-card-body"><h3>{video.title || video.source_url}</h3><p>{video.uploader || connectorLabel(video.connector)}</p><div className="video-keyword-list">{video.keywords.map((keyword) => <button type="button" key={keyword} onClick={() => selectKeyword(keyword)}>{keyword}</button>)}</div><button className="card-open" onClick={() => navigate(`watch/${video.id}`)}><Icon name="watch" size={15} />Watch<Icon name="arrowRight" size={14} /></button></div>
      </article>)}</div> : <div className="collection-empty"><Icon name="search" size={28} /><h3>No videos match this keyword</h3><p>Choose another word from the cloud or try a broader search.</p></div>}
    </section>}
  </div>;
}
