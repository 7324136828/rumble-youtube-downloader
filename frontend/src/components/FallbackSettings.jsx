import React, { useEffect, useState } from 'react';
import { useRecommendations } from './RecommendationContext';
import { DEFAULT_FALLBACK_WEIGHTS } from '../recommendationUtils';
import Icon from './Icon';

const SOURCES = [['custom_search', 'Website search'], ['watch_later', 'Watch later']];

export default function FallbackSettings() {
  const { settings, loading, saving, updateSettings } = useRecommendations();
  const weightsKey = JSON.stringify(settings.fallback_weights || DEFAULT_FALLBACK_WEIGHTS);
  const [weights, setWeights] = useState(() => JSON.parse(weightsKey));
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => { setWeights(JSON.parse(weightsKey)); }, [weightsKey]);
  const total = SOURCES.reduce((sum, [id]) => sum + Number(weights[id] || 0), 0);
  const save = async (event) => {
    event.preventDefault();
    setError(''); setNotice('');
    const values = { ...Object.fromEntries(SOURCES.map(([id]) => [id, Number(weights[id])])), public_search: 0 };
    if (SOURCES.some(([id]) => weights[id] === '') || Object.values(values).some((value) => !Number.isInteger(value) || value < 0 || value > 100) || total !== 100) {
      setError('Use whole percentages from 0 to 100 that add up to 100%.'); return;
    }
    try { await updateSettings({ fallback_weights: values }); setNotice('Fallback percentages saved.'); }
    catch (err) { setError(err.message || 'Could not save fallback percentages.'); }
  };
  return <section className="recommendation-settings-card" aria-labelledby="recommendation-fallback-heading">
    <div className="recommendation-card-heading"><span className="recommendation-card-icon"><Icon name="grid" size={22} /></span><div><h2 id="recommendation-fallback-heading">Fallback recommendation mix</h2><p>Choose the balance when your model returns no usable picks.</p></div></div>
    <p className="recommendation-help">If the model returns no picks or is unavailable, videos are chosen at random using these percentages. An empty source's share is divided among the other sources with a positive percentage. Set a source to 0% to exclude it from fallback picks.</p>
    <form onSubmit={save}>
      <div className="recommendation-weight-fields">{SOURCES.map(([id, name]) => <label className="recommendation-field" key={id}>{name} (%)<input aria-label={`${name} fallback percent`} type="number" min="0" max="100" step="1" value={weights[id]} onChange={(event) => setWeights((previous) => ({ ...previous, [id]: event.target.value }))} disabled={loading || saving} required /></label>)}</div>
      <p className="recommendation-help" role="status">Total: {total}% of 100%</p>
      {error && <p className="recommendation-field-error" role="alert">{error}</p>}
      {notice && <p className="recommendation-help" role="status">{notice}</p>}
      <button className="btn-primary" disabled={loading || saving}>Save fallback mix</button>
    </form>
  </section>;
}
