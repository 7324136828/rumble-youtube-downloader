import { useEffect } from 'react';
import { recordWatchHistory } from '../services/api';

const REPORT_INTERVAL_MS = 12000;
const MAX_DELTA_SECONDS = 30;

/** Observe the active player only; preloaded videos never contribute history. */
export default function useWatchHistory(videoId, getPlayer, enabled = true) {
  useEffect(() => {
    if (!videoId || !enabled) return undefined;
    const player = getPlayer(videoId);
    if (!player) return undefined;

    let startedAt = null;
    let pendingSeconds = 0;
    let hasPlayed = false;
    let completionSent = false;
    let leaving = false;
    const foreground = () => document.visibilityState !== 'hidden' && !leaving;
    const canPlay = () => foreground() && !player.paused && !player.ended && !player.seeking && player.readyState >= 3;
    const collect = () => {
      if (startedAt === null) return;
      const now = performance.now();
      const elapsed = (now - startedAt) / 1000;
      // Bound time lost to suspended browser timers; seeking never adds time.
      if (Number.isFinite(elapsed) && elapsed > 0) pendingSeconds += Math.min(elapsed, MAX_DELTA_SECONDS);
      startedAt = now;
    };
    const stop = () => { collect(); startedAt = null; };
    const resume = () => {
      if (startedAt === null && canPlay()) {
        hasPlayed = true;
        startedAt = performance.now();
      }
    };
    const flush = (completed = false, keepalive = false) => {
      collect();
      if (!hasPlayed) return;
      const position = player.currentTime;
      if (!Number.isFinite(position) || position < 0) return;
      const finish = completed && !completionSent;
      if (pendingSeconds <= 0 && !finish) return;
      // A delayed timer can leave multiple small playback segments to send.
      let remaining = pendingSeconds;
      pendingSeconds = 0;
      if (finish) completionSent = true;
      do {
        const seconds = Math.min(MAX_DELTA_SECONDS, remaining);
        remaining -= seconds;
        // History is best effort. Retrying a possibly committed delta would
        // count the same viewing time twice without an idempotency key.
        recordWatchHistory(videoId, {
          positionSeconds: position,
          watchedSeconds: seconds,
          completed: finish && remaining <= 0,
        }, { keepalive }).catch(() => {});
      } while (remaining > 0);
    };
    const pause = () => { stop(); flush(false, true); };
    const ended = () => { stop(); flush(true, true); };
    const seeked = () => resume();
    const visibility = () => {
      if (foreground()) resume();
      else { stop(); flush(false, true); }
    };
    const pagehide = () => { leaving = true; stop(); flush(false, true); };
    const pageshow = () => { leaving = false; resume(); };
    const listeners = {
      playing: resume,
      pause,
      ended,
      waiting: stop,
      seeking: stop,
      seeked,
      error: pause,
      emptied: pause,
    };
    Object.entries(listeners).forEach(([name, listener]) => player.addEventListener(name, listener));
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', pagehide);
    window.addEventListener('pageshow', pageshow);
    const timer = window.setInterval(() => {
      if (!canPlay()) stop();
      flush();
    }, REPORT_INTERVAL_MS);
    // Autoplay may have started before this effect attaches its listeners.
    resume();

    return () => {
      stop();
      flush(false, true);
      window.clearInterval(timer);
      Object.entries(listeners).forEach(([name, listener]) => player.removeEventListener(name, listener));
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', pagehide);
      window.removeEventListener('pageshow', pageshow);
    };
  }, [videoId, getPlayer, enabled]);
}
