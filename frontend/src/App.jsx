import React, { useEffect, useState } from 'react';
import ConvertPage from './components/ConvertPage';
import HistoryPage from './components/HistoryPage';
import VideosPage from './components/VideosPage';

function screenFromHash() {
  const [name, arg] = window.location.hash.replace(/^#\/?/, '').split('/');
  if (name === 'history' || name === 'videos') {
    return { screen: name, jobId: null };
  }
  return { screen: 'convert', jobId: arg || null };
}

export default function App() {
  const [route, setRoute] = useState(screenFromHash);

  useEffect(() => {
    const onHashChange = () => setRoute(screenFromHash());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const navigate = (hash) => {
    window.location.hash = hash;
  };

  const goMonitor = (jobId) => navigate(`convert/${jobId}`);

  return (
    <div className="app">
      <nav className="topnav">
        <span className="brand">Rumble / YouTube Converter</span>
        <button
          className={route.screen === 'convert' ? 'nav-btn active' : 'nav-btn'}
          onClick={() => navigate('convert')}
        >
          Convert
        </button>
        <button
          className={route.screen === 'videos' ? 'nav-btn active' : 'nav-btn'}
          onClick={() => navigate('videos')}
        >
          Videos
        </button>
        <button
          className={route.screen === 'history' ? 'nav-btn active' : 'nav-btn'}
          onClick={() => navigate('history')}
        >
          History
        </button>
      </nav>
      <main className="content">
        {route.screen === 'convert' && (
          <ConvertPage
            monitorJobId={route.jobId}
            onMonitorJob={(jobId) => navigate(`convert/${jobId}`)}
          />
        )}
        {route.screen === 'videos' && <VideosPage />}
        {route.screen === 'history' && (
          <HistoryPage
            onContinueJob={goMonitor}
            onWatchVideos={() => navigate('videos')}
          />
        )}
      </main>
    </div>
  );
}
