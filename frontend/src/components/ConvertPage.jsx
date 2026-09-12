import React, { useEffect, useState } from 'react';
import UrlInput, { extractUrls } from './UrlInput';
import OptionsPanel, { DEFAULT_OPTIONS, buildPayload } from './OptionsPanel';
import JobStatus from './JobStatus';
import { createJob } from '../services/api';

export default function ConvertPage({ monitorJobId, onMonitorJob }) {
  const [urls, setUrls] = useState([]);
  const [options, setOptions] = useState(DEFAULT_OPTIONS);
  const [jobId, setJobId] = useState(monitorJobId);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    setJobId(monitorJobId);
  }, [monitorJobId]);

  useEffect(() => {
    const handlePaste = (e) => {
      const found = extractUrls(e.clipboardData?.getData('text') || '');
      if (found.length) setUrls((prev) => [...new Set([...prev, ...found])]);
    };
    window.addEventListener('paste', handlePaste);
    return () => window.removeEventListener('paste', handlePaste);
  }, []);

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      const result = await createJob(urls, buildPayload(options));
      setJobId(result.job_id);
      onMonitorJob?.(result.job_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="convert-page">
      <h2>Convert Rumble / YouTube media</h2>
      <p className="muted">
        Audio is downloaded as MP3; transcripts are produced from subtitles first,
        with local Whisper speech recognition as fallback.
      </p>
      <UrlInput urls={urls} onChange={setUrls} />
      <OptionsPanel options={options} onChange={setOptions} />
      {error && <pre className="error-banner">{error}</pre>}
      <button
        className="btn-primary"
        disabled={!urls.length || submitting}
        onClick={submit}
      >
        {submitting ? 'Starting…' : 'Start conversion'}
      </button>
      {jobId && (
        <JobStatus jobId={jobId} onDiscard={() => setJobId(null)} />
      )}
    </div>
  );
}
