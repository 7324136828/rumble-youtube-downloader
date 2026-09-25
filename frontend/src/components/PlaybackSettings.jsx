import React from 'react';
import { PLAYBACK_SPEEDS, updatePlaybackPreferences, usePlaybackPreferences } from '../playbackPreferences';
import './PlaybackSettings.css';

export default function PlaybackSettings() {
  const { muted, speed } = usePlaybackPreferences();
  return <section className="playback-settings" aria-labelledby="playback-settings-heading">
    <h2 id="playback-settings-heading">Playback</h2>
    <p>Your preferences are saved in this browser and apply to audio and video in Watch and Feed.</p>
    <div className="playback-setting-row"><div><label htmlFor="playback-start-sound">Start playback with sound</label><p id="playback-sound-help">When enabled, new videos start unmuted. Your browser may ask you to press play first. The player’s mute button also saves this preference.</p></div><input id="playback-start-sound" type="checkbox" role="switch" checked={!muted} aria-describedby="playback-sound-help" onChange={(event) => updatePlaybackPreferences({ muted: !event.target.checked })} /></div>
    <div className="playback-setting-row"><div><label htmlFor="playback-default-speed">Playback speed</label><p id="playback-speed-help">Changing speed here or in a player keeps that speed across different videos.</p></div><select id="playback-default-speed" value={speed} aria-describedby="playback-speed-help" onChange={(event) => updatePlaybackPreferences({ speed: Number(event.target.value) })}>{PLAYBACK_SPEEDS.map((rate) => <option key={rate} value={rate}>{rate}x</option>)}</select></div>
    <small>Changes save automatically.</small>
  </section>;
}
