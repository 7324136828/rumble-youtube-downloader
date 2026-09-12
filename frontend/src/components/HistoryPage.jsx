import React, { useEffect, useState } from 'react';
import { getJobs, discardJob, downloadZipUrl } from '../services/api';
import FileButtons from './FileButtons';

function formatSize(bytes) {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function elapsed(job) {
  if (!job.completed_at) return '—';
  const ms = new Date(job.completed_at) - new Date(job.created_at);
  return `${Math.max(0, Math.round(ms / 1000))}s`;
}

export default function HistoryPage({ onContinueJob, onWatchVideos }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadJobs = async () => {
    try {
      setJobs(await getJobs());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadJobs();
    const timer = setInterval(loadJobs, 4000);
    return () => clearInterval(timer);
  }, []);

  const handleDiscard = async (jobId) => {
    if (!window.confirm('Discard this in-progress job and purge its temp folder?'))
      return;
    await discardJob(jobId);
    loadJobs();
  };

  return (
    <div className="history-screen">
      <h2>Conversion History</h2>
      {loading ? (
        <p className="muted">Loading history…</p>
      ) : jobs.length === 0 ? (
        <p className="muted">No conversions yet.</p>
      ) : (
        <table className="jobs-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Size</th>
              <th>Created</th>
              <th>Elapsed</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} className={`status-${job.status}`}>
                <td className="job-name" title={job.filename}>{job.filename}</td>
                <td>{formatSize(job.file_size)}</td>
                <td>{new Date(job.created_at).toLocaleString()}</td>
                <td>{elapsed(job)}</td>
                <td>
                  <span className={`badge badge-${job.status}`}>
                    {job.status.replace('_', ' ')}
                  </span>
                </td>
                <td>
                  {(job.status === 'in_progress' || job.status === 'queued') && (
                    <div className="btn-group">
                      <button
                        className="btn-continue"
                        onClick={() => onContinueJob(job.id)}
                      >
                        Continue
                      </button>
                      <button
                        className="btn-discard"
                        onClick={() => handleDiscard(job.id)}
                      >
                        Discard
                      </button>
                    </div>
                  )}
                  {job.status === 'completed' && (
                    <div className="btn-group">
                      <button
                        className="btn-continue"
                        onClick={() => onWatchVideos?.()}
                      >
                        Watch
                      </button>
                      <FileButtons
                        files={job.files}
                        zipUrl={downloadZipUrl(job.id)}
                      />
                    </div>
                  )}
                  {(job.status === 'failed' || job.status === 'discarded') && (
                    <span className="muted">{job.status}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
