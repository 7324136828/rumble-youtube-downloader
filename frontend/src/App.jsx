import React, { useEffect, useState } from "react";
import FeedPage from "./components/FeedPage";
import WatchPage from "./components/WatchPage";
import LibraryPage from "./components/LibraryPage";
import ConnectorsPage from "./components/ConnectorsPage";
import ConvertPage from "./components/ConvertPage";
import HistoryPage from "./components/HistoryPage";
import VideosPage from "./components/VideosPage";
import Icon from "./components/Icon";
const SCREENS = new Set(["feed", "watch", "library", "liked", "downloads", "connectors", "convert", "history", "videos"]);
const LABELS = { feed: "Swipe feed", watch: "Watch", library: "My library", liked: "Liked videos", downloads: "Downloads", connectors: "Connectors", convert: "Convert media", history: "Conversion history", videos: "Converted videos" };
function screenFromHash() {
  const [name, id] = window.location.hash.replace(/^#\/?/, "").split("/");
  return { screen: SCREENS.has(name) ? name : "library", id: id || null };
}
export default function App() {
  const [route, setRoute] = useState(screenFromHash);
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    const change = () => {
      setRoute(screenFromHash());
      setMenuOpen(false);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  const navigate = (hash) => {
    window.location.hash = hash;
    setMenuOpen(false);
  };
  const addVideo = () => {
    navigate("library/add");
    window.setTimeout(() => document.getElementById("import-urls")?.focus(), 100);
  };
  const navItem = (screen, icon, label) => <button key={screen} className={`side-link ${route.screen === screen ? "is-active" : ""}`} onClick={() => navigate(screen)} aria-current={route.screen === screen ? "page" : void 0}><Icon name={icon} /><span>{label || LABELS[screen]}</span>{screen === "feed" && <span className="nav-new">PLAY</span>}</button>;
  return <div className="app"><a className="skip-link" href="#main-content" onClick={(event) => {
    event.preventDefault();
    document.getElementById("main-content")?.focus();
  }}>Skip to content</a>{menuOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}<aside className={`sidebar ${menuOpen ? "is-open" : ""}`}><button className="brand" onClick={() => navigate("library")} aria-label="ClipFeed home"><span className="brand-symbol"><Icon name="play" size={23} /></span><span>clip<span className="brand-light">feed</span><span className="brand-dot">.</span></span></button><div className="workspace-label">YOUR VIDEO SPACE</div><nav aria-label="Main navigation"><div className="side-group">{navItem("library", "grid")}{navItem("feed", "feed")}{navItem("watch", "watch")}</div><div className="side-divider" /><div className="side-group">{navItem("liked", "heart")}{navItem("downloads", "download")}{navItem("connectors", "link")}</div></nav><div className="sidebar-bottom"><div className="sidebar-tip"><span className="tip-icon"><Icon name="bolt" size={17} /></span><strong>Your videos. Your way.</strong><p>Bring your favorites together.<br />Watch at your own pace.</p><button onClick={addVideo}>Add a video <Icon name="arrowRight" size={16} /></button></div><details className="tools-menu"><summary><Icon name="settings" size={17} /> More tools</summary>{navItem("convert", "refresh")}{navItem("history", "history")}{navItem("videos", "folder")}</details><div className="local-profile"><span className="profile-avatar">C</span><div><strong>Personal library</strong><span>Stored on this device</span></div><span className="status-dot" /></div></div></aside><div className="app-body"><header className="app-header"><div className="header-location"><button className="icon-btn mobile-menu" onClick={() => setMenuOpen(!menuOpen)} aria-label="Toggle navigation" aria-expanded={menuOpen}><Icon name="menu" /></button><span className="breadcrumb-root">Workspace</span><span className="breadcrumb-slash">/</span><span>{LABELS[route.screen]}</span></div><div className="header-actions"><div className="global-search"><Icon name="search" size={17} /><input aria-label="Search your library" placeholder="Search your library" value={search} onChange={(e) => {
    setSearch(e.target.value);
    if (!["library", "liked"].includes(route.screen)) navigate("library");
  }} />{search && <button className="icon-btn" aria-label="Clear search" onClick={() => setSearch("")}><Icon name="close" size={14} /></button>}</div><button className="btn-primary header-add" onClick={addVideo}><Icon name="plus" size={17} /><span>Add videos</span></button></div></header><main id="main-content" className="main-content" tabIndex={-1}>{route.screen === "feed" && <FeedPage videoId={route.id} navigate={navigate} />}{route.screen === "watch" && <WatchPage videoId={route.id} navigate={navigate} />}{["library", "liked", "downloads"].includes(route.screen) && <LibraryPage navigate={navigate} screen={route.screen} search={search} focusImport={route.id === "add"} />}{route.screen === "connectors" && <ConnectorsPage navigate={navigate} />}{["convert", "history", "videos"].includes(route.screen) && <div className="content legacy-content">{route.screen === "convert" && <ConvertPage monitorJobId={route.id} onMonitorJob={(id) => navigate(`convert/${id}`)} />}{route.screen === "history" && <HistoryPage onContinueJob={(id) => navigate(`convert/${id}`)} onWatchVideos={() => navigate("videos")} />}{route.screen === "videos" && <VideosPage />}</div>}</main></div></div>;
}
