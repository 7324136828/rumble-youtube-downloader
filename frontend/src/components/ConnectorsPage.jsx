import React, { useEffect, useState } from "react";
import Icon from "./Icon";
import { getConnectors, resolveUrls } from "../services/api";
const DETAILS = {
  youtube: { icon: "youtube", text: "Videos, Shorts, and your favorite creators. Save a video and choose how you want to watch.", tags: ["Videos", "Shorts"] },
  rumble: { icon: "rumble", text: "Bring Rumble videos into your personal library with the same simple download flow.", tags: ["Videos", "Creators"] },
  generic: { icon: "globe", text: "Try other video sites through yt-dlp. Availability depends on the site, video, and access requirements.", tags: ["Vimeo", "Twitch", "Other sites"] }
};
export default function ConnectorsPage({ navigate }) {
  const [connectors, setConnectors] = useState(null);
  const [error, setError] = useState("");
  const [url, setUrl] = useState("");
  const [result, setResult] = useState(null);
  const [checking, setChecking] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let live = true;
    setError("");
    getConnectors().then((data) => {
      if (live) setConnectors(data);
    }).catch((err) => {
      if (live) setError(err.message);
    });
    return () => {
      live = false;
    };
  }, [retry]);
  async function check(event) {
    event.preventDefault();
    setChecking(true);
    setResult(null);
    setError("");
    try {
      const data = await resolveUrls([url.trim()]);
      setResult(data[0]?.connector ? `This URL uses the ${data[0].connector.name} connector. Video availability is checked when downloading.` : "No connector accepts this URL. Use a public HTTP or HTTPS video page.");
    } catch (err) {
      setError(err.message);
    } finally {
      setChecking(false);
    }
  }
  return <div className="page-wrap connectors-page"><div className="page-heading"><div><p className="eyebrow">BRING IT ALL TOGETHER</p><h1>A home for every source<span className="accent">.</span></h1><p className="page-description">One library. A growing world of video.</p></div><span className="soft-pill"><Icon name="link" size={15} /> Connector powered</span></div>{error && <div className="error-banner" role="alert">{error} <button className="link-btn" onClick={() => setRetry((value) => value + 1)}>Retry connection</button></div>}<div className="connector-grid">{connectors?.map((connector) => {
    const detail = DETAILS[connector.id] || DETAILS.generic;
    return <article className={`connector-card source-${connector.id}`} key={connector.id}><div className="connector-card-top"><span className="platform-icon"><Icon name={detail.icon} size={29} /></span><span className="connector-available"><span className="status-dot" />Available</span></div><h2>{connector.name}</h2><p>{detail.text}</p><div className="connector-tags">{detail.tags.map((tag) => <span key={tag}>{tag}</span>)}</div><button className="btn-secondary" onClick={() => navigate("library/add")}>Add a video <Icon name="arrowRight" size={16} /></button></article>;
  })}</div>{!connectors && !error && <p className="muted" role="status">Loading connectors…</p>}<section className="connector-check"><div><span className="eyebrow">FIND YOUR CONNECTION</span><h2>Have a link in mind?</h2><p className="muted">See which connector handles your video URL.</p></div><form onSubmit={check}><label htmlFor="connector-url" className="sr-only">Video URL</label><div className="connector-check-input"><Icon name="link" size={18} /><input id="connector-url" type="url" placeholder="https://…" required value={url} onChange={(event) => {
    setUrl(event.target.value);
    setResult(null);
  }} /><button className="btn-primary" disabled={checking}>{checking ? "Checking\u2026" : "Check link"}</button></div>{result && <p className="connection-result" role="status">{result}</p>}</form></section><div className="connector-note"><Icon name="settings" /><div><strong>Built to grow with your library</strong><p>Each platform uses a separate connector. New sources can be added without changing your library or players.</p></div></div></div>;
}
