import { useEffect, useState } from 'react';

export const PLAYBACK_SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const CHANGE_EVENT = 'clipfeed:playback-preferences';
let sessionPreferences = { muted: true, volume: 1, speed: 1 };
let storageWriteFailed = false;

export function readPlaybackPreferences() {
  if (storageWriteFailed) return sessionPreferences;
  try {
    const speed = Number(localStorage.getItem('clipfeed.speed'));
    const storedVolume = localStorage.getItem('clipfeed.volume');
    const volume = storedVolume === null || storedVolume === '' ? NaN : Number(storedVolume);
    return {
      muted: localStorage.getItem('clipfeed.muted') !== '0',
      volume: Number.isFinite(volume) && volume >= 0 && volume <= 1 ? volume : 1,
      speed: PLAYBACK_SPEEDS.includes(speed) ? speed : 1,
    };
  } catch { return sessionPreferences; }
}

export function updatePlaybackPreferences(changes) {
  const current = readPlaybackPreferences();
  sessionPreferences = {
    muted: typeof changes.muted === 'boolean' ? changes.muted : current.muted,
    volume: typeof changes.volume === 'number' && Number.isFinite(changes.volume) && changes.volume >= 0 && changes.volume <= 1 ? changes.volume : current.volume,
    speed: PLAYBACK_SPEEDS.includes(changes.speed) ? changes.speed : current.speed,
  };
  try {
    localStorage.setItem('clipfeed.muted', sessionPreferences.muted ? '1' : '0');
    localStorage.setItem('clipfeed.volume', String(sessionPreferences.volume));
    localStorage.setItem('clipfeed.speed', String(sessionPreferences.speed));
  } catch { storageWriteFailed = true; }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function usePlaybackPreferences() {
  const [preferences, setPreferences] = useState(readPlaybackPreferences);
  useEffect(() => {
    const sync = () => setPreferences(readPlaybackPreferences());
    window.addEventListener(CHANGE_EVENT, sync);
    window.addEventListener('storage', sync);
    sync();
    return () => {
      window.removeEventListener(CHANGE_EVENT, sync);
      window.removeEventListener('storage', sync);
    };
  }, []);
  return preferences;
}
