# Rumble / YouTube Converter

A full-stack web app that downloads audio from Rumble and YouTube videos,
generates transcripts (subtitles first, local Whisper speech recognition as
fallback), and optionally preserves the full video for in-browser playback.

The legacy command-line pipeline lives untouched in `skill/original-project/`
and is invoked per job inside an isolated system temp folder — the repository
stays clean.

## Screenshots

### Convert

Paste URLs (`Ctrl+V`), drop a `url.json` / `.txt` file, or browse for one.
Conversion settings cover Whisper model, language, device, workers, chunk
size, GPU indices, VAD, transcript, and video-retention flags.

![Convert screen](docs/screenshots/convert.png)

### Job monitor

Live status badge, per-file downloads (transcript `.txt` / `.vtt`, audio
`.mp3`, video `.mp4` / `.mkv`, or everything as `.zip`), inline video
playback, and a collapsible execution log.

![Job monitor](docs/screenshots/job-monitor.png)

### Video library

Every completed job that kept its video appears here. Press **Prepare for
playback** to remux to MP4 (fast `-c copy`, transcoding fallback), then play
in the browser — multiple videos supported.

![Video library](docs/screenshots/videos.png)

### History

Persistent ledger of all jobs (SQLite-backed): status badges, elapsed time,
**Continue**/**Discard** for in-progress jobs, and per-file downloads for
completed ones. Auto-refreshes every few seconds.

![Conversion history](docs/screenshots/history.png)

## Features

- **Batch URL conversion** — multiple Rumble/YouTube URLs per job; failures
  are isolated per URL
- **Subtitle-first transcripts** — uses VTT subtitles when available, falls
  back to OpenAI Whisper (with optional Silero VAD filtering)
- **Isolated job execution** — every job runs under
  `tempfile.gettempdir()/prod_jobs/<uuid>`; nothing is written into the repo
- **Crash-resumable** — the underlying pipeline checkpoints audio chunks and
  ASR results atomically
- **In-browser video playback** — MKV sources are remuxed/transcoded to MP4
  lazily and served with HTTP range (seekable) streaming
- **Per-file downloads** — transcript, audio, video, or full ZIP
- **Deep-linkable screens** — `#/convert`, `#/videos`, `#/history`, and
  `#/convert/<job-id>` to resume monitoring a job

## Quick start

Prerequisites: Python ≥ 3.10, Node.js + npm, FFmpeg + ffprobe on PATH.

```powershell
# Windows
setup.bat
run.bat

# Linux / macOS
./setup.sh
./run.sh
```

- `setup` creates `.venv`, installs `backend/requirements.txt`
  (FastAPI, yt-dlp, openai-whisper, silero-vad, torch), runs `npm install`
  in `frontend/`, and seeds `.env` from `.env.example`.
- `run` launches the backend at <http://localhost:8000> (API docs at
  `/docs`) and the frontend at <http://localhost:5173>.

## Project layout

```text
├── original-project via skill/original-project/   # legacy pipeline (preserved)
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI routes
│   │   ├── config.py          # env-driven settings
│   │   ├── services/          # pipeline adapter, media prep, job DB
│   │   ├── utils/             # system temp dir lifecycle
│   │   └── schemas/           # Pydantic models
│   ├── requirements.txt
│   └── tests/                 # python -m unittest tests.test_backend
├── frontend/                  # Vite + React SPA
├── setup.py / setup.bat / setup.sh
├── run.bat / run.sh
└── secrets.md                 # secret-scan audit trail
```

## API overview

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/convert` | Create job from `{urls, options}` |
| GET | `/api/jobs` · `/api/jobs/{id}` | Job list / detail (incl. `files`) |
| GET | `/api/jobs/{id}/logs` | Execution log |
| GET | `/api/jobs/{id}/download?file=` | Per-file download |
| GET | `/api/jobs/{id}/download-zip` | All outputs as ZIP |
| GET | `/api/jobs/{id}/videos` · `/api/videos` | Video listing |
| GET | `/api/jobs/{id}/stream?file=` | Range-streamed playback (202 while preparing) |
| POST | `/api/jobs/{id}/discard` | Abort job, purge temp folder |

## Configuration

All optional; see `.env.example`: `BACKEND_HOST`, `BACKEND_PORT`,
`ORIGINAL_PROJECT_DIR`, `JOBS_DB_PATH`, `JOBS_ROOT`.
