import React, { useRef, useState } from 'react';

const URL_RE = /https?:\/\/[^\s"'<>)\]]+/g;

export function extractUrls(text) {
  const found = text.match(URL_RE) || [];
  return [...new Set(found.map((u) => u.replace(/[.,;]+$/, '')))];
}

export default function UrlInput({ urls, onChange, hint }) {
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);

  const mergeUrls = (incoming) => {
    if (!incoming.length) return;
    onChange([...new Set([...urls, ...incoming])]);
  };

  const handleText = (text) => mergeUrls(extractUrls(text));

  const handleFile = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => handleText(String(reader.result || ''));
    reader.readAsText(file);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer?.files?.[0];
    if (file) {
      handleFile(file);
    } else {
      handleText(e.dataTransfer?.getData('text') || '');
    }
  };

  return (
    <div
      className={dragging ? 'dropzone dragging' : 'dropzone'}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <p className="dropzone-hint">
        {hint ?? (
          <>
            Paste URLs below (Ctrl+V), drop a url.json / .txt file here, or{' '}
            <button
              type="button"
              className="link-btn"
              onClick={() => fileRef.current?.click()}
            >
              browse for a file
            </button>
            .
          </>
        )}
      </p>
      <input
        ref={fileRef}
        type="file"
        accept=".json,.txt,text/plain,application/json"
        hidden
        onChange={(e) => {
          handleFile(e.target.files?.[0]);
          e.target.value = '';
        }}
      />
      <textarea
        className="url-textarea"
        rows={6}
        placeholder={'https://rumble.com/VIDEO.html\nhttps://youtu.be/VIDEO'}
        value={urls.join('\n')}
        onChange={(e) =>
          onChange(e.target.value.split('\n').map((u) => u.trim()).filter(Boolean))
        }
      />
      <p className="url-count">{urls.length} URL(s) queued</p>
    </div>
  );
}
