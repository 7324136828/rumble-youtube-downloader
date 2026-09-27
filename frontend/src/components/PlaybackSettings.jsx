import React from 'react';
import { PLAYBACK_SPEEDS, updatePlaybackPreferences, usePlaybackPreferences } from '../playbackPreferences';
import './PlaybackSettings.css';

export default function PlaybackSettings() {
  const { muted, volume, speed } = usePlaybackPreferences();
  return <section className="playback-settings" aria-labelledby="playback-settings-heading">
    <h2 id="playback-settings-heading">Playback</h2>
    <p>Your preferences are saved in this browser and apply to audio and video in Watch and Feed.</p>
    <div className="playback-setting-row"><div><label htmlFor="playback-start-sound">Start playback with sound</label><p id="playback-sound-help">When enabled, new videos start unmuted. Your browser may ask you to press play first. The player’s mute button also saves this preference.</p></div><input id="playback-start-sound" type="checkbox" role="switch" checked={!muted} aria-describedby="playback-sound-help" onChange={(event) => updatePlaybackPreferences({ muted: !event.target.checked, ...(event.target.checked && volume === 0 ? { volume: 0.5 } : {}) })} /></div>
    <div className="playback-setting-row"><div><label htmlFor="playback-default-volume">Volume</label><p id="playback-volume-help">Set the volume used across Watch and Feed. Changing it in a player saves the same level.</p></div><div className="playback-volume-control"><input id="playback-default-volume" type="range" min="0" max="1" step="0.05" value={volume} aria-describedby="playback-volume-help" aria-valuetext={`${Math.round(volume * 100)} percent`} onChange={(event) => { const next = Number(event.target.value); updatePlaybackPreferences({ volume: next, muted: next === 0 }); }} /><output htmlFor="playback-default-volume">{Math.round(volume * 100)}%</output></div></div>
    <div className="playback-setting-row"><div><label htmlFor="playback-default-speed">Playback speed</label><p id="playback-speed-help">Changing speed here or in a player keeps that speed across different videos.</p></div><select id="playback-default-speed" value={speed} aria-describedby="playback-speed-help" onChange={(event) => updatePlaybackPreferences({ speed: Number(event.target.value) })}>{PLAYBACK_SPEEDS.map((rate) => <option key={rate} value={rate}>{rate}x</option>)}</select></div>
    <small>Changes save automatically.</small>
  </section>;
}
