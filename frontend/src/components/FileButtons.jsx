import React from 'react';

const KIND_ORDER = { transcript: 0, audio: 1, video: 2, other: 3 };

function label(file) {
  const ext = file.name.split('.').pop().toUpperCase();
  if (file.kind === 'transcript') return ext === 'TXT' ? 'TXT' : ext;
  if (file.kind === 'audio') return ext;
  if (file.kind === 'video') return ext;
  return ext || 'FILE';
}

export default function FileButtons({ files, zipUrl }) {
  const sorted = [...(files || [])].sort(
    (a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.name.localeCompare(b.name)
  );
  return (
    <div className="btn-group">
      {sorted.map((file) => (
        <a
          key={file.path}
          className={`btn-file btn-${file.kind}`}
          href={file.download_url}
          download
          title={file.name}
        >
          {label(file)}
        </a>
      ))}
      {zipUrl && (
        <a className="btn-file btn-zip" href={zipUrl} download title="All outputs as ZIP">
          ZIP
        </a>
      )}
    </div>
  );
}
