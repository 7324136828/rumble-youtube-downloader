import React, { useEffect, useRef, useState } from 'react';
import { getJob, getJobLogs, getJobVideos, discardJob, downloadZipUrl } from '../services/api';
import VideoCard from './VideoCard';
import FileButtons from './FileButtons';

const STAGES = [
  'Queued',
  'Streaming audio',
  'Transcribing',
  'Writing transcript',
  'Packaging ZIP',
  'Done',
];

function stageIndex(progress) {
  if (progress >= 100) return 5;
  if (progress >= 88) return 4;
  if (progress >= 50) return 3;
  if (progress >= 15) return 2;
  if (progress >= 5) return 1;
  return 0;
}

export default function JobStatus({ jobId, onDiscard }) {
  const [job, setJob] = useState(null);
  const [logs, setLogs] = useState('');
  const [videos, setVideos] = useState([]);
  const [showLogs, setShowLogs] = useState(false);
  const logRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const [j, l] = await Promise.all([getJob(jobId), getJobLogs(jobId)]);
        if (cancelled) return;
        setJob(j);
        setLogs(l);
        if (j.status === 'completed') {
          setVideos(await getJobVideos(jobId));
        }
      } catch {
        /* transient poll errors ignored */
      }
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [jobId]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs, showLogs]);

  if (!job) return <p className="muted">Connecting to job…</p>;

  const active = job.status === 'queued' || job.status === 'in_progress';
  const stage = stageIndex(job.progress || 0);

  return (
    <div className="job-status">
      <div className="job-status-header">
        <span className={`badge badge-${job.status}`}>{job.status.replace('_', ' ')}</span>
        <span className="job-name">{job.filename}</span>
        {active && (
          <button
            className="btn-discard"
            onClick={async () => {
              if (window.confirm('Discard this job and purge its temp folder?')) {
                await discardJob(jobId);
                onDiscard?.();
              }
            }}
          >
            Discard
          </button>
        )}
      </div>

      {active && (
        <>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${job.progress}%` }} />
          </div>
          <ol className="stage-list">
            {STAGES.map((label, i) => (
              <li
                key={label}
                className={i < stage ? 'stage done' : i === stage ? 'stage current' : 'stage'}
              >
                {label}
              </li>
            ))}
          </ol>
        </>
      )}

      {job.status === 'completed' && (
        <>
          <div className="download-row">
            <span className="muted">Download:</span>
            <FileButtons files={job.files} zipUrl={downloadZipUrl(jobId)} />
          </div>
          {videos.length > 0 && (
            <div className="video-grid">
              {videos.map((video) => (
                <VideoCard key={video.path} video={video} />
              ))}
            </div>
          )}
        </>
      )}
      {job.status === 'failed' && (
        <pre className="error-banner">{job.error_message || 'Job failed'}</pre>
      )}
      {job.status === 'discarded' && <p className="muted">Job discarded; temp folder purged.</p>}

      <button className="link-btn" onClick={() => setShowLogs(!showLogs)}>
        {showLogs ? 'Hide logs' : 'Show logs'}
      </button>
      {showLogs && (
        <pre ref={logRef} className="log-viewer">
          {logs || 'No log output yet.'}
        </pre>
      )}
    </div>
  );
}
