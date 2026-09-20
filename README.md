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

The default screen is **My library**. The sidebar also provides **Search videos**, **Swipe feed**,
**Watch**, **Liked videos**, **Downloads**, **Recommendations**, **Settings**, and **Connectors**. Legacy conversion,
conversion history, and converted-video tools remain under **More tools**.

1. Paste one video URL per line in the library. Use Shift+Enter for another line.
2. Choose Best available, 1080p, 720p, or 480p and select **Add videos**.
3. Follow progress in the library or Downloads. Failed downloads show the reason
   and offer retry or dismissal; active downloads can be cancelled.
4. Open a completed video in Watch or Swipe. The selected player preference is
   remembered in your browser.

To find videos without a link, use the search bar in **My library** or open
**Search videos**. Enter keywords, choose YouTube, Rumble, or both, and select
**Search**. Results show thumbnails, titles, creators, and durations when available.
Select a download quality and **Add to library** to queue a result, or **Open
original** to view it on its source site. Searching alone does not download videos.
Search queries and platform choices stay in the page URL for refresh and back/forward
navigation. The header's **Search your library** field filters saved videos.

Online search requires internet access and uses public platform results without
API keys. A platform failure displays a warning while keeping results from the other
platform available; failed searches can be retried.

For personalized suggestions, open **Recommendations**, select an active model
configuration from The Connector or upload its `config.json`, and turn on
**AI recommendations**. Add interests to start an empty library. The model uses
local watch history to derive topics, search YouTube and Rumble, and choose an
ordered playlist from verified results. Suggestions appear in an empty/end-of-list
Swipe feed and alongside Watch's Up next list. Select **Play** for saved videos or
**Download & play** for new ones. The header switch turns suggestions off anytime;
they are off by default. See the [setup, algorithm, and tool contracts](docs/recommendations.md).

**AI picks for you** appears beside the Watch player, with a reason for each
suggestion and **Play** or **Download & play** actions. This screenshot uses the
actual interface with demo recommendation data.

![ClipFeed AI picks beside the Watch player, with recommendation reasons, Play, and Download & play buttons](docs/screenshots/clipfeed-ai-picks.png)

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

Fullscreen and picture-in-picture controls use the standard browser APIs when
available and fall back to Safari's native video presentation APIs on iPhone and
iPad. Compact layouts keep both controls visible.

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

### LAN access and custom ports

To use the app from another computer or phone on the same network:

```powershell
.\run_lan.bat
# Choose separate frontend and backend ports:
.\run_lan.bat --frontend-port 5174 --backend-port 8001
# Equivalent command:
.\run.bat serve --lan --frontend-port 5174 --backend-port 8001
```

On Linux/macOS, use `./run.sh serve --lan --frontend-port 5174 --backend-port 8001`.
The `serve` command is optional when calling `run.bat`, `run.sh`, or `run.py` directly.
Port flags also work without `--lan`.

LAN mode listens on all IPv4 interfaces and prints LAN URLs. Open the frontend
URL, such as `http://192.168.1.20:5174`, on the other device. The frontend proxies
API requests, playback, thumbnails, and downloads to the chosen backend port.
LAN devices share the host's library and controls; the app has no authentication.
If the page cannot be reached, allow Node.js (or the frontend TCP port) through
Windows Firewall on your private network and check that Wi-Fi client isolation
is disabled. Direct API access also requires allowing the backend TCP port.

Command-line ports override environment variables and `.env`. Explicit ports must
be different and available; otherwise startup fails with an error. Without port
flags, `FRONTEND_PORT` (default `5173`) and `BACKEND_PORT` (default `8000`) remain
starting ports, with automatic selection of the next available port.

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

The backend validates downloaded media with ffprobe and keeps the original format
by default. WebM files stream directly to the custom Watch and Swipe players with
`video/webm` and byte-range seeking. VP8, VP9, and AV1 video with Opus or Vorbis
audio are recognized for native WebM playback. Actual decoding depends on the
browser and device; see [WebM codec support](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Containers#webm).
Playback does not automatically start MP4 conversion.

Open **Settings** to control optional processing for new downloads:

- **Convert downloads to MP4**: off by default. Explicitly enable this and save to
  convert new non-MP4 downloads, including WebM, to H.264/AAC MP4. Compatible MP4
  files pass through. Conversion may take time for large or high-resolution files.
  With the switch off, formats outside the supported browser codec set retain
  their original files and show a notice with an external-download link.
- **Generate missing thumbnails**: turn off to skip creating a preview frame.
  Thumbnails already supplied by the source are kept either way.

Thumbnail generation defaults on. Click **Save settings** to persist choices in SQLite.
Existing installations clear the old default-on conversion setting once, requiring
a fresh opt-in; subsequent choices survive restarts. Thumbnail preferences remain.
Downloads already queued or running keep their original settings. Combining
separate source video/audio tracks remains necessary for a complete download.
Legacy on-demand playback conversion also requires opt-in. Explicit conversion and
transcription jobs under More tools keep their separate options.

Downloads and recommendation buttons show the current preparation step, such as
**Combining video and audio**, **Checking browser compatibility**, or **Converting
for browser playback**. Processing uses an indeterminate progress indicator: the
old fixed 92% was a stage marker, not measured conversion progress. A long 4K
conversion can continue working for some time after the network transfer finishes.

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
| GET | `/api/search?q=&source=all&limit=12` | Search YouTube/Rumble metadata; `source` is `all`, `youtube`, or `rumble`; `limit` is 1–24 |
| GET / POST | `/api/watch-history` | Read recent history or record actual playback |
| GET / PATCH | `/api/settings/downloads` | Read/update `convert_for_browser` and `generate_thumbnails` for new downloads |
| GET / PATCH | `/api/recommendations/settings` | Read/update enablement, selected model, and interests |
| GET | `/api/recommendations/models` | Discover active Connector configurations |
| POST | `/api/recommendations/configs` | Validate and import `{name, config}` into The Connector |
| GET | `/api/recommendations/tools` | List metadata and keyword-search function schemas |
| POST | `/api/recommendations/tools/{name}` | Invoke a search tool with `{arguments}` when enabled |
| POST | `/api/recommendations` | Generate an ordered playlist for Feed or Watch |
| POST | `/api/resolve` | Route `{urls}` to connectors; does not check download availability |
| POST | `/api/media` | Start downloads with `{urls, quality}`; returns queued items |
| GET | `/api/media` / `/api/media/{id}` | Library list (`?status=`) or item detail |
| DELETE | `/api/media/{id}` | Cancel pending work and remove the item/files |
| GET | `/api/media/{id}/stream` | Seekable playback; 409 until ready |
| GET | `/api/media/{id}/thumbnail` | Thumbnail; 404 if unavailable |
| GET | `/api/media/{id}/download` | File attachment; 409 until ready |
| POST | `/api/media/{id}/conversions/{format}` | Start an on-demand `mp4` or `mp3` conversion |
| GET | `/api/media/{id}/conversions/{format}/download` | Download a completed derived file |
| GET | `/api/media/{id}/conversions/mp4/stream` | Seekable playback of a completed compatible MP4 |
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
`error_message`, `playback_warning`, `stream_url`, `thumbnail_url`, `download_url`,
`file_name`, and persistent `conversions` state for MP4 and MP3 outputs.

Routes include `#/library`, `#/library/add`, `#/search`, `#/recommendations`, `#/settings`, `#/feed`, `#/feed/<id>`, `#/watch`,
`#/watch/<id>`, `#/liked`, `#/downloads`, `#/connectors`, `#/convert`,
`#/convert/<job-id>`, `#/history`, and `#/videos`.

## Configuration and storage

See `.env.example` for backend options: `BACKEND_HOST`, `BACKEND_PORT`,
`ORIGINAL_PROJECT_DIR`, `JOBS_DB_PATH`, `JOBS_ROOT`, `MEDIA_ROOT`, and
`MAX_CONCURRENT_DOWNLOADS`, and `RECOMMENDATION_CONNECTOR_URL` (default
`http://127.0.0.1:8301`). The runner also accepts `FRONTEND_PORT` as its starting
frontend port.

Downloaded videos, SQLite metadata, and legacy working folders default to the
operating system's temporary directory under `prod_jobs`. On Windows:

- Videos and thumbnails: `%TEMP%\prod_jobs\library`
- Database for jobs, videos, watch history, and app preferences: `%TEMP%\prod_jobs\jobs.db`
- Legacy working folders: `%TEMP%\prod_jobs\<job-id>`

`JOBS_ROOT` relocates this storage root; `MEDIA_ROOT` and `JOBS_DB_PATH` can override
the media and database locations individually. Unset or empty values use the
defaults. Files remain between app restarts but may be removed by system temporary
file cleanup. Set `JOBS_ROOT` to a durable directory if you want permanent storage.
For an existing installation, stop the app and move `backend/jobs.db` to the new
database location before restarting to retain its library records (do not replace
an existing destination database). The default download concurrency is two.

## Verification

From the repository root on Windows:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:player
npm.cmd --prefix frontend run test:app
npm.cmd --prefix frontend run test:webm
```

On Linux/macOS, use `.venv/bin/python` and `npm` in the same commands.
Browser tests require Chrome, Edge, or Chromium installed locally. Set
`PLAYER_TEST_BROWSER` to the executable path when it cannot be found automatically.
The browser suites pass **66 checks**, covering player behavior, app navigation,
Feed/Watch/search, watch history, recommendations, and download settings. They run against isolated
Vite servers and deterministic API/media
fixtures; they do not change your library or download external content.
The `test:webm` suite uses real browser media APIs to decode, play, and seek local
VP9/Opus and AV1/Opus WebM fixtures through `CustomVideoPlayer`; both passed in
Chrome 153. Its fixture-generation commands are recorded in
[`frontend/tests/native-webm.fixtures.md`](frontend/tests/native-webm.fixtures.md).

The backend suite passes **114 offline tests**. It uses fake connectors and videos
generated locally with FFmpeg, covering connector routing, playlist rejection,
queued/active cancellation, MP4 codec conversion, thumbnails, library persistence,
API download-to-stream lifecycle, byte-range seeking, and legacy job behavior.
Processing tests cover saved preferences, migration of existing library records,
retaining original files when conversion is off, queued-job settings, optional
thumbnails, and bounded thumbnail failures. Native playback tests cover AV1/VP9
WebM files kept unchanged, explicit MP4 opt-in, MIME types, and byte-range responses.
Search tests cover provider metadata, safe result URLs, query bounds, Rumble query
encoding, HTTP error diagnostics, timeouts, empty results, and partial failures;
browser checks also cover search-to-download
actions and stale search responses.
Recommendation tests cover model discovery/import, history-to-keywords-to-playlist
generation, tool calling, verified candidates, round-robin search, settings races,
cache reuse, and disabling pending requests. Model responses are mocked; no paid
provider inference is part of the suite.
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
    routers/             Recommendation settings, models, tools, and playlists
    services/            Downloads, search, recommendations, SQLite, legacy jobs
    utils/               Working-directory lifecycle
    schemas/             API request models
  tests/                 Offline API, connector, library, and media regressions
frontend/
  src/
    App.jsx              Sidebar, search, and route shell
    components/          Library, players, feed, watch, recommendation settings
    hooks/               Playback-history tracking
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
