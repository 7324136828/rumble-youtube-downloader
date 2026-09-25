# Native WebM fixtures

`native-webm.vp9.webm` and `native-webm.av1.webm` are two-second, 96 x 64
synthetic color patterns with a generated 440 Hz tone. No third-party footage
or audio is included. The committed files total less than 36 KB.

Generate them from the repository root with FFmpeg:

```powershell
ffmpeg -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=96x64:rate=12:duration=2' -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=2' -c:v libvpx-vp9 -b:v 60k -c:a libopus -b:a 24k -y frontend/tests/native-webm.vp9.webm
ffmpeg -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=96x64:rate=12:duration=2' -f lavfi -i 'sine=frequency=440:sample_rate=48000:duration=2' -c:v libaom-av1 -cpu-used 8 -crf 40 -b:v 0 -c:a libopus -b:a 24k -y frontend/tests/native-webm.av1.webm
```

Run `npm run test:webm` from `frontend`. This uses the installed Chromium
browser, an isolated temporary profile, a local Vite server, and real-time
native media decoding. It never mocks `HTMLMediaElement` or downloads media.

The same suite includes `native-audio.mp3`, a two-second synthetic tone used to
verify audio playback and seeking with persistent thumbnail artwork:

```powershell
ffmpeg -hide_banner -loglevel error -nostdin -y -f lavfi -i sine=frequency=440:duration=2 -c:a libmp3lame -q:a 5 frontend/tests/native-audio.mp3
```
