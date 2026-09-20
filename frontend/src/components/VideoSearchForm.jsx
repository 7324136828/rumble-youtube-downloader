import React, { useEffect, useState } from 'react';
import Icon from './Icon';
import './VideoSearch.css';

export function videoSearchRoute(query, source = 'all') {
  return `search?${new URLSearchParams({ q: query.trim(), source })}`;
}

export default function VideoSearchForm({ query = '', source = 'all', onSearch }) {
  const [text, setText] = useState(query);
  const [platform, setPlatform] = useState(source);

  useEffect(() => { setText(query); setPlatform(source); }, [query, source]);

  const submit = (event) => {
    event.preventDefault();
    if (text.trim()) onSearch(text.trim(), platform);
  };

  return <form className="video-search-form" aria-label="Search online videos" role="search" onSubmit={submit}>
    <div className="video-search-input">
      <Icon name="search" size={21} />
      <input type="search" aria-label="Search YouTube and Rumble" placeholder="Search videos, topics, or creators…" maxLength={200} value={text} onChange={(event) => setText(event.target.value)} />
    </div>
    <select aria-label="Search platform" value={platform} onChange={(event) => setPlatform(event.target.value)}>
      <option value="all">YouTube + Rumble</option>
      <option value="youtube">YouTube</option>
      <option value="rumble">Rumble</option>
    </select>
    <button className="btn-primary" type="submit" disabled={!text.trim()}><Icon name="search" size={16} />Search</button>
  </form>;
}
