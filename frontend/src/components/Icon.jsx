import React from 'react';

const paths = {
  play: 'm9 5 11 7-11 7V5Z', pause: 'M8 5v14M16 5v14',
  feed: 'M8 3h8a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm2 5 5 4-5 4V8Z',
  watch: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm6 4 6 4-6 4V8Z',
  library: 'M4 6v14M9 4v16M14 4v16M18 5l4 14',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4',
  plus: 'M12 5v14M5 12h14', search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  link: 'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',
  heart: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z',
  volume: 'M11 4 6 8H2v8h4l5 4V4Zm4 4a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14',
  muted: 'M11 4 6 8H2v8h4l5 4V4Zm5 5 6 6m0-6-6 6',
  chevronUp: 'm6 15 6-6 6 6', chevronDown: 'm6 9 6 6 6-6', arrowRight: 'M4 12h16m-6-6 6 6-6 6',
  check: 'm5 12 4 4L19 6', trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  close: 'm6 6 12 12M6 18 18 6', settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',
  history: 'M3 10a9 9 0 1 1 1 7M3 3v7h7m2-4v6l4 2',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3a18 18 0 0 1 0 18 18 18 0 0 1 0-18Z',
  youtube: 'M21 7a3 3 0 0 0-2-2 50 50 0 0 0-14 0 3 3 0 0 0-2 2 30 30 0 0 0 0 10 3 3 0 0 0 2 2 50 50 0 0 0 14 0 3 3 0 0 0 2-2 30 30 0 0 0 0-10ZM10 9l5 3-5 3V9Z',
  rumble: 'M5 5c0-2 2-3 4-2l11 7c2 1 2 3 0 4L9 21c-2 1-4 0-4-2V5Zm4 3v8l6-4-6-4Z',
  grid: 'M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z',
  refresh: 'M20 7a9 9 0 0 0-16-1L2 9m0-6v6h6m-4 8a9 9 0 0 0 16 1l2-3m0 6v-6h-6',
  external: 'M14 3h7v7m0-7L10 14M10 3H4a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6',
  folder: 'M3 7V5a2 2 0 0 1 2-2h5l2 4h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
  menu: 'M4 6h16M4 12h16M4 18h16', bolt: 'm13 2-9 12h7l-1 8L21 9h-8l0-7Z',
};

export default function Icon({ name, size = 20, className = '', ...props }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true" {...props}><path d={paths[name] || paths.play} /></svg>;
}
