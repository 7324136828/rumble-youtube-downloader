import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { getRecommendationSettings, updateRecommendationSettings } from '../services/api';
import { DEFAULT_FALLBACK_WEIGHTS, DEFAULT_RECOMMENDATION_PROVIDERS } from '../recommendationUtils';
import './Recommendations.css';

const DEFAULT_SETTINGS = { enabled: false, model_id: null, seed_keywords: [], custom_prompt: '', providers: DEFAULT_RECOMMENDATION_PROVIDERS, fallback_weights: DEFAULT_FALLBACK_WEIGHTS, allow_unverified_links: false, allow_ai_title_lookup: false, fetch_all_search_links: false, revision: 0 };
const RecommendationContext = createContext({
  settings: DEFAULT_SETTINGS, settingsLoaded: true, enabled: false, loading: false, saving: false, error: '', revision: 0,
  updateSettings: async () => DEFAULT_SETTINGS, reloadSettings: () => {}, openSettings: () => {},
  savedWatchLaterUrls: new Set(), noteWatchLaterSaved: () => {}, noteWatchLaterRemoved: () => {},
});

export function useRecommendations() {
  return useContext(RecommendationContext);
}

export function RecommendationProvider({ children, navigate }) {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [reload, setReload] = useState(0);
  const [savedWatchLaterUrls, setSavedWatchLaterUrls] = useState(() => new Set());
  const mounted = useRef(false);
  const mutation = useRef(0);
  const queue = useRef(Promise.resolve());

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const version = mutation.current;
    setLoading(true);
    setError('');
    getRecommendationSettings(controller.signal).then((result) => {
      if (!controller.signal.aborted && version === mutation.current) {
        setSettings({ ...DEFAULT_SETTINGS, ...result });
        setSettingsLoaded(true);
        setRevision((value) => value + 1);
      }
    }).catch((err) => {
      if (!controller.signal.aborted && version === mutation.current) setError(err.message || 'Could not load recommendation settings.');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [reload]);

  const updateSettings = useCallback((patch) => {
    const version = ++mutation.current;
    // Invalidate displayed playlists at once, before a slow settings request.
    setRevision((value) => value + 1);
    setSaving(true);
    setError('');
    if (patch.enabled === false) setSettings((previous) => ({ ...previous, enabled: false }));
    // Serial PATCHes ensure a quick off-switch cannot be overtaken by an older save.
    const operation = queue.current.catch(() => {}).then(() => updateRecommendationSettings(patch));
    queue.current = operation;
    return operation.then((result) => {
      if (mounted.current && version === mutation.current) {
        setSettings({ ...DEFAULT_SETTINGS, ...result });
        setSettingsLoaded(true);
        setRevision((value) => value + 1);
      }
      return result;
    }).catch((err) => {
      if (mounted.current && version === mutation.current) setError(err.message || 'Could not save recommendation settings.');
      throw err;
    }).finally(() => {
      if (mounted.current && version === mutation.current) setSaving(false);
    });
  }, []);

  const openSettings = useCallback(() => {
    if (navigate) navigate('recommendations');
    else window.location.hash = 'recommendations';
  }, [navigate]);
  const reloadSettings = useCallback(() => setReload((value) => value + 1), []);
  const noteWatchLaterSaved = useCallback((urls) => setSavedWatchLaterUrls((previous) => new Set([...previous, ...urls])), []);
  const noteWatchLaterRemoved = useCallback((url) => setSavedWatchLaterUrls((previous) => { const next = new Set(previous); next.delete(url); return next; }), []);
  return <RecommendationContext.Provider value={{
    settings, settingsLoaded, enabled: Boolean(settings.enabled) && !loading && !saving,
    loading, saving, error, revision, updateSettings, reloadSettings, openSettings,
    savedWatchLaterUrls, noteWatchLaterSaved, noteWatchLaterRemoved,
  }}>{children}</RecommendationContext.Provider>;
}

export function RecommendationToggle({ compact = false }) {
  const { settings, loading, saving, error, updateSettings, openSettings } = useRecommendations();
  const [needsModel, setNeedsModel] = useState(false);
  const toggle = (event) => {
    if (event.target.checked && !settings.model_id) { setNeedsModel(true); openSettings(); return; }
    setNeedsModel(false);
    updateSettings({ enabled: event.target.checked }).catch(() => {});
  };
  return <div className={`recommendation-toggle-wrap ${compact ? 'is-compact' : ''}`}>
    <label className="recommendation-toggle">
      <span className="recommendation-toggle-label">AI recommendations</span>
      {compact && <span className="recommendation-toggle-short" aria-hidden="true">AI</span>}
      <input type="checkbox" role="switch" checked={Boolean(settings.enabled)} disabled={loading || (saving && !settings.enabled)} onChange={toggle} />
      <span className="recommendation-switch" aria-hidden="true" />
    </label>
    {error && <button className="recommendation-toggle-error" onClick={openSettings} title={error}>Settings unavailable</button>}
    {!error && needsModel && !settings.model_id && <button className="recommendation-toggle-hint" onClick={openSettings}>Choose a model first</button>}
  </div>;
}
