# ClipFeed - your video library, two ways to watch

ClipFeed downloads individual videos through platform connectors and puts them in
one local library. Open a video in a YouTube-style **Watch** view or browse with
up/down swipes in the TikTok-style **Swipe feed**. Both views use custom React
playback controls around an inline HTML video element, without native player
controls or platform embeds.

YouTube and Rumble have dedicated connectors. A generic yt-dlp connector tries
other supported sites. Matching a connector identifies how to handle a URL; it
does not guarantee that the source is downloadable.

## Screenshots

Captured from the running local app with saved videos.

**My library** brings downloaded videos, source filters, search, and playback
choices together.

![ClipFeed desktop library with saved videos and platform filters](docs/screenshots/clipfeed-library.png)

**Watch** pairs a widescreen video with custom playback controls, video details,
and an Up next list.

![ClipFeed Watch view with custom video controls and an Up next list](docs/screenshots/clipfeed-watch.png)

**Swipe feed** lets you move between videos vertically while preserving each
video's aspect ratio.

![ClipFeed Swipe feed with an aspect-preserving video and custom controls](docs/screenshots/clipfeed-feed.png)

<details>
<summary>Connectors and downloads</summary>

**Connectors** shows the dedicated YouTube and Rumble connectors alongside the
generic yt-dlp connector.

![ClipFeed platform connectors](docs/screenshots/clipfeed-connectors.png)

**Downloads** shows the queue and completed saved videos. This capture contains
completed downloads.

![ClipFeed download queue with completed videos](docs/screenshots/clipfeed-downloads.png)

</details>

**On mobile**, the library, Watch view, and Swipe feed adapt to a narrow screen.

<p>
  <img src="docs/screenshots/clipfeed-library-mobile.png" width="250" alt="ClipFeed library on mobile">
  <img src="docs/screenshots/clipfeed-watch-mobile.png" width="250" alt="ClipFeed custom Watch player on mobile">
  <img src="docs/screenshots/clipfeed-feed-mobile.png" width="250" alt="ClipFeed Swipe feed on mobile">
</p>

## Using the app

The default screen is **My library**. The sidebar also provides **Swipe feed**,
**Watch**, **Liked videos**, **Downloads**, and **Connectors**. Legacy conversion,
conversion history, and converted-video tools remain under **More tools**.

1. Paste one video URL per line in the library. Use Shift+Enter for another line.
2. Choose Best available, 1080p, 720p, or 480p and select **Add videos**.
3. Follow progress in the library or Downloads. Failed downloads show the reason
   and offer retry or dismissal; active downloads can be cancelled.
4. Open a completed video in Watch or Swipe. The selected player preference is
   remembered in your browser.

The collection contains your downloaded videos, with real thumbnails and source
metadata when available. Search titles, creators, and URLs; filter by platform;
sort newest first, oldest first, or by title. Heart a video to find it in Liked
videos. Download a saved file to your browser or delete it from the library.

Files and SQLite metadata live on the machine running the backend. Likes and
player preferences use the current browser's local storage. There are no accounts
or cloud synchronization. Copy link copies the original source URL. In-app video routes refer to this
running app and require access to the same backend library.

## Custom players

**Watch** provides a widescreen player, title and creator information, description,
an Up next list, and optional autoplay-next. **Swipe feed** provides a vertical
scroll-snap player with touch, wheel, previous/next buttons, and up/down keyboard
navigation. Only the active feed item plays; landscape videos retain their aspect
ratio inside the vertical presentation.

The shared `CustomVideoPlayer` provides:

- Play/pause, seek timeline, elapsed/total time, and buffered progress.
- Mute and volume controls, plus playback speeds from 0.5x to 2x.
- Fullscreen and picture-in-picture buttons when the browser exposes those APIs.
- Keyboard-accessible controls, loading states, playback errors, and retry.
- Inline playback and muted autoplay fallback when browser autoplay policy requires it.

When the player is focused: Space or K toggles playback, M toggles mute, F toggles
fullscreen, Left/Right seeks five seconds, and J/L seeks ten seconds. Up/Down
adjusts volume in Watch and changes videos in the feed. Touch scrolling moves
between feed items. Autoplay, programmatic volume, fullscreen, and picture-in-picture
availability can differ between browsers and devices.

The browser still decodes video using its media APIs; the in-page interface is
custom-designed in `frontend/src/components/CustomVideoPlayer.jsx` and its CSS.

## Quick start

Install Python 3.10+, Node.js 22+ with npm, and FFmpeg with ffprobe on PATH.
FFmpeg must include the `libx264` and AAC encoders. The backend requires
`yt-dlp[default,curl-cffi]>=2026.8.19`; setup installs this requirement.

YouTube extraction uses yt-dlp's EJS scripts and a supported JavaScript runtime.
The `default` extra includes `yt-dlp-ejs`; the connector enables Node and Deno.
Node 22+ satisfies the documented Node runtime requirement. See the official
[yt-dlp dependencies](https://github.com/yt-dlp/yt-dlp#dependencies) and
[EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

```powershell
# Windows, from the repository root
.\setup.bat
.\run.bat
```

```sh
# Linux / macOS, from the repository root
./setup.sh
./run.sh
```

Setup uses an active Python environment or creates `.venv`, installs the backend
requirements and frontend npm dependencies, and seeds `.env` from `.env.example`.
The requirements retain Whisper and Silero VAD for the legacy converter.

The runner normally starts the frontend at <http://localhost:5173> and backend at
<http://127.0.0.1:8000>, with API docs at `/docs`. If a port is occupied, it selects
another available port and prints the actual URLs. The frontend API proxy targets
the selected backend. Stop both services with Ctrl+C.

## Connectors and download lifecycle

Platform connectors live under `backend/app/connectors/`. `registry.resolve(url)`
uses the first matching connector: YouTube, Rumble, then generic yt-dlp. The player
and library consume the same `DownloadResult`/`VideoInfo` contract for every source.

For a site already supported by yt-dlp, add a connector such as:

```python
# backend/app/connectors/vimeo.py
from .ytdlp import YtDlpConnector


class VimeoConnector(YtDlpConnector):
    id = "vimeo"
    name = "Vimeo"
    domains = ("vimeo.com",)
```

Then import and insert `VimeoConnector()` before `GenericConnector()` in
`registry.py`'s `CONNECTORS` list. Override `ydl_opts` or `format_for` if the source
needs platform-specific options. For a different download implementation, subclass
`Connector`, implement `download(url, dest_dir, quality, on_progress, cancel)`, and
return a `DownloadResult` with a file inside `dest_dir` and its `VideoInfo`. Report
progress through the callback and honor the cancellation event.

Downloads run in background threads bounded by `MAX_CONCURRENT_DOWNLOADS`. A video
moves through `queued`, `downloading`, `processing`, and `ready`, or ends as `failed`
with an error message. The API hides internal filesystem paths. An interrupted
server restart marks unfinished downloads failed so they can be retried.

The backend inspects actual video and audio codecs with ffprobe before marking a
file ready. Compatible MP4/WebM videos can play directly; other supported files
are remuxed or transcoded to H.264/AAC MP4 with fast-start metadata. This avoids
assuming every file ending in `.mp4` can play in the browser. Preparation may take
time for large or high-resolution videos and may change the downloaded encoding.

Streaming supports full responses, single byte ranges, suffix ranges, and proper
206/416 responses for seeking. Queued cancellation prevents a download from
starting; active work observes cancellation and stops FFmpeg preparation. Cleanup
runs after an active worker exits. File routes restrict legacy downloads to their
job output directories, and database connections close after each operation.

### Platform limits

- Import individual video links. Playlist and channel results are rejected before
  yt-dlp starts downloading their entries.
- The generic connector delegates to yt-dlp. An HTTP(S) URL can match it even when
  no extractor can download that particular page.
- DRM-protected, private, authenticated, region-restricted, removed, or
  platform-blocked videos are not guaranteed to work. This app does not add a
  login/cookie flow or DRM decryption.
- Requested quality is a preference constrained by available source formats and
  the connector's fallback selection.
- Extraction depends on upstream platform behavior. Keep yt-dlp and its matching
  EJS dependencies updated when a platform changes.

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/connectors` | List connectors (`id`, `name`, `domains`) |
| POST | `/api/resolve` | Route `{urls}` to connectors; does not check download availability |
| POST | `/api/media` | Start downloads with `{urls, quality}`; returns queued items |
| GET | `/api/media` / `/api/media/{id}` | Library list (`?status=`) or item detail |
| DELETE | `/api/media/{id}` | Cancel pending work and remove the item/files |
| GET | `/api/media/{id}/stream` | Seekable playback; 409 until ready |
| GET | `/api/media/{id}/thumbnail` | Thumbnail; 404 if unavailable |
| GET | `/api/media/{id}/download` | File attachment; 409 until ready |
| POST | `/api/convert` | Create legacy job from `{urls, options}` |
| GET | `/api/jobs` / `/api/jobs/{id}` | Legacy job list/detail, including files |
| GET | `/api/jobs/{id}/logs` | Execution log |
| GET | `/api/jobs/{id}/download?file=` | Per-file legacy download |
| GET | `/api/jobs/{id}/download-zip` | Legacy outputs as ZIP |
| GET | `/api/jobs/{id}/videos` / `/api/videos` | Legacy video listing |
| GET | `/api/jobs/{id}/stream?file=` | Legacy playback; 202 while preparing |
| POST | `/api/jobs/{id}/discard` | Abort legacy job and purge its temporary folder |

Media items expose `id`, `source_url`, `connector`, `status`, `progress`, `stage`,
`quality`, available title/creator/duration/dimensions, timestamps, file size,
`error_message`, `stream_url`, `thumbnail_url`, `download_url`, and `file_name`.

Routes include `#/library`, `#/library/add`, `#/feed`, `#/feed/<id>`, `#/watch`,
`#/watch/<id>`, `#/liked`, `#/downloads`, `#/connectors`, `#/convert`,
`#/convert/<job-id>`, `#/history`, and `#/videos`.

## Configuration and storage

See `.env.example` for backend options: `BACKEND_HOST`, `BACKEND_PORT`,
`ORIGINAL_PROJECT_DIR`, `JOBS_DB_PATH`, `JOBS_ROOT`, `MEDIA_ROOT`, and
`MAX_CONCURRENT_DOWNLOADS`. The runner also accepts `FRONTEND_PORT` as its starting
frontend port.

The SQLite database defaults to `backend/jobs.db`. Legacy working folders default
to the operating system's temporary directory under `prod_jobs`. The media library
defaults to `prod_jobs/library` there. Set **`MEDIA_ROOT` to a durable directory**
if downloaded videos should survive operating-system temporary-file cleanup.
Back up that directory together with the SQLite database to preserve the library.
The default download concurrency is two.

## Verification

From the repository root on Windows:

```powershell
.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:player
npm.cmd --prefix frontend run test:app
```

On Linux/macOS, use `.venv/bin/python` and `npm` in the same commands.
Browser tests require Chrome, Edge, or Chromium installed locally. Set
`PLAYER_TEST_BROWSER` to the executable path when it cannot be found automatically.
The browser suites pass **32 checks** (14 player checks and 18 app/Feed/Watch
checks). They run against isolated Vite servers and deterministic API/media
fixtures; they do not change your library or download external content.

The backend suite passes **30 offline tests**. It uses fake connectors and videos
generated locally with FFmpeg, covering connector routing, playlist rejection,
queued/active cancellation, MP4 codec conversion, thumbnails, library persistence,
API download-to-stream lifecycle, byte-range seeking, and legacy job behavior.
It does not download videos from external platforms; live YouTube/Rumble extraction
is not covered by this verification. Browser smoke checks also confirmed custom
playback of existing saved YouTube, Rumble, and generic-source videos, plus
390-pixel mobile layouts, actual touch swipes in both directions, and pausing
offscreen feed videos.

The backend tests ran on Windows with Python 3.14.6, FFmpeg 9.0.1, yt-dlp 2026.8.19,
yt-dlp-ejs 0.8.0, and curl-cffi 0.16.3. Node 24.18.0 and npm 11.16.0 are available in
the same development environment. These record the tested environment, not an
assertion that all other versions have been tested.

## Project layout

```text
backend/
  app/
    main.py              FastAPI routes
    config.py            Environment settings
    connectors/          Connector contract, registry, platform implementations
    services/            Download library, codec preparation, SQLite, legacy jobs
    utils/               Working-directory lifecycle
    schemas/             API request models
  tests/                 Offline API, connector, library, and media regressions
frontend/
  src/
    App.jsx              Sidebar, search, and route shell
    components/          Library, custom players, feed, watch, and legacy screens
    mediaUtils.js        Display helpers and browser-local preferences
setup.py / setup.bat / setup.sh
run.py / run.bat / run.sh
skill/original-project/  Optional legacy pipeline location
```

## Legacy converter screenshots and features

These screenshots document the older conversion tools, not the new Library,
Watch, and Swipe feed interface.

### Convert

Paste URLs (Ctrl+V), drop a `url.json` / `.txt` file, or browse for one.
Conversion settings cover Whisper model, language, device, workers, chunk size,
GPU indices, VAD, transcript, and video-retention flags. Batch conversion isolates
failures per URL. Transcription uses available VTT subtitles first, with Whisper
and optional Silero VAD as the fallback.

![Convert screen](docs/screenshots/convert.png)

### Job monitor

Live status badges, per-file downloads (transcript `.txt` / `.vtt`, audio `.mp3`,
video `.mp4` / `.mkv`, or everything as `.zip`), inline playback, and a collapsible
execution log.

![Job monitor](docs/screenshots/job-monitor.png)

### Converted videos

Completed legacy jobs that retained their video appear here. Prepare supported
files for browser playback through FFmpeg when needed; multiple videos per job
are supported.

![Legacy video library](docs/screenshots/videos.png)

### History

SQLite-backed conversion history shows status, elapsed time, Continue/Discard for
in-progress jobs, and completed file downloads. It refreshes automatically.

![Conversion history](docs/screenshots/history.png)

### Legacy script availability

Starting a legacy conversion requires `skill/original-project/download_rumble.py`
or an `ORIGINAL_PROJECT_DIR` override containing that script. It is absent in this
checkout, so the backend logs a warning and `POST /api/convert` returns **503**.
Existing job history remains viewable. Connector-based Library, Watch, and Swipe
feed work independently of the legacy script.
