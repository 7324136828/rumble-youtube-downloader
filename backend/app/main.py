"""FastAPI entrypoint and route declarations."""
import json
import logging
import mimetypes
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               Response, StreamingResponse)

from . import config
from .connectors import registry
from .routers.recommendations import router as recommendations_router
from .routers.settings import router as settings_router
from .schemas.job import ConvertRequest
from .schemas.media import DownloadRequest, ResolveRequest
from .schemas.search import SearchResponse, SearchSource
from .schemas.watch_history import WatchEvent
from .services import db, library, media, pipeline, search
from .utils import temp_manager

app = FastAPI(title="Rumble/YouTube Conversion API")
app.include_router(recommendations_router)
app.include_router(settings_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    db.init_db()
    db.fail_stale_jobs()
    db.fail_stale_videos()
    if not config.DOWNLOADER_SCRIPT.is_file():
        logging.getLogger(__name__).warning(
            "original-project script not found: %s — /api/convert is disabled",
            config.DOWNLOADER_SCRIPT)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/watch-history")
def watch_history(limit: int = Query(30, ge=1, le=100)):
    return db.list_watch_history(limit)


@app.post("/api/watch-history")
def record_watch_history(event: WatchEvent):
    try:
        row = db.record_watch(**event.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"recorded": row is not None, "entry": row}


@app.post("/api/convert")
def start_conversion(request: ConvertRequest):
    if not config.DOWNLOADER_SCRIPT.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Legacy converter script not found at "
                   f"{config.DOWNLOADER_SCRIPT}; the Convert feature is "
                   f"unavailable.")
    urls = [u.strip() for u in request.urls if isinstance(u, str) and u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="At least one URL is required.")

    job_id = str(uuid.uuid4())
    job_dir = temp_manager.create_job_dir(job_id)
    urls_file = job_dir / "inputs" / "urls.json"
    payload = json.dumps(urls, ensure_ascii=False, indent=2)
    urls_file.write_text(payload, encoding="utf-8")

    filename = request.filename or (
        urls[0] if len(urls) == 1 else f"{len(urls)} URLs")
    job = db.create_job(
        job_id=job_id,
        filename=filename,
        file_size=len(payload.encode("utf-8")),
        temp_dir=job_dir,
        urls=urls,
        params=request.options.model_dump(exclude_none=True),
    )
    pipeline.start_job_thread(job_id)
    return {"job_id": job["id"], "status": job["status"]}


@app.get("/api/jobs")
def list_jobs():
    jobs = db.list_jobs()
    for job in jobs:
        job["files"] = _files_payload(job)
    return jobs


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["files"] = _files_payload(job)
    return job


@app.get("/api/jobs/{job_id}/download")
def download_file(job_id: str, file: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    target = _resolve_download(job, file)
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(path=target, filename=target.name, media_type=media_type)


@app.get("/api/jobs/{job_id}/logs", response_class=PlainTextResponse)
def get_job_logs(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    log_path = Path(job["log_path"]) if job.get("log_path") else None
    if not log_path or not log_path.is_file():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace")


@app.post("/api/jobs/{job_id}/discard")
def discard(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] in ("completed", "failed", "discarded"):
        raise HTTPException(status_code=400,
                            detail=f"Job is already {job['status']}")
    pipeline.discard_job(job_id)
    return {"job_id": job_id, "status": "discarded",
            "detail": "Temporary folder purged"}


_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".wav", ".flac"}
_TRANSCRIPT_EXTS = {".txt", ".vtt", ".srt"}


def _kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in media.VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _TRANSCRIPT_EXTS:
        return "transcript"
    return "other"


def _files_payload(job: dict) -> list:
    job_dir = Path(job["temp_dir"])
    outputs = job_dir / "outputs"
    files = []
    if outputs.is_dir():
        for f in sorted(outputs.rglob("*")):
            rel_parts = f.relative_to(outputs).parts
            if (not f.is_file() or f.name.endswith(".partial.txt")
                    or any(p.startswith(".") for p in rel_parts)):
                continue
            rel = f.relative_to(outputs).as_posix()
            files.append({
                "name": f.name,
                "path": rel,
                "size": f.stat().st_size,
                "kind": _kind_of(f),
                "download_url": (f"/api/jobs/{job['id']}"
                                 f"/download?file={quote(rel)}"),
            })
    for video in media.find_videos(job_dir):
        stream = media.stream_path_for(video)
        if stream.is_file() and stream != video:
            rel = f"stream/{stream.name}"
            files.append({
                "name": stream.name,
                "path": rel,
                "size": stream.stat().st_size,
                "kind": "video",
                "download_url": (f"/api/jobs/{job['id']}"
                                 f"/download?file={quote(rel)}"),
            })
    return files


def _resolve_download(job: dict, file: str) -> Path:
    if file.startswith("stream/"):
        base = (Path(job["temp_dir"]) / "work").resolve()
        rel = file[len("stream/"):]
    else:
        base = (Path(job["temp_dir"]) / "outputs").resolve()
        rel = file
    target = (base / rel).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


def _videos_payload(job: dict) -> list:
    job_dir = Path(job["temp_dir"])
    outputs = job_dir / "outputs"
    videos = []
    for video in media.find_videos(job_dir):
        rel = video.relative_to(outputs).as_posix()
        videos.append({
            "name": video.name,
            "path": rel,
            "size": video.stat().st_size,
            "state": media.video_state(job_dir, video),
            "stream_url": f"/api/jobs/{job['id']}/stream?file={quote(rel)}",
        })
    return videos


@app.get("/api/jobs/{job_id}/videos")
def list_job_videos(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _videos_payload(job)


@app.get("/api/videos")
def list_all_videos():
    result = []
    for job in db.list_jobs():
        if job["status"] != "completed":
            continue
        for video in _videos_payload(job):
            result.append({**video, "job_id": job["id"],
                           "job_name": job["filename"],
                           "created_at": job["created_at"]})
    return result


@app.get("/api/jobs/{job_id}/stream")
def stream_video(job_id: str, file: str, request: Request):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    outputs = (Path(job["temp_dir"]) / "outputs").resolve()
    target = (outputs / file).resolve()
    if (not target.is_relative_to(outputs)
            or not target.is_file()
            or target.suffix.lower() not in media.VIDEO_EXTS):
        raise HTTPException(status_code=404, detail="Video not found")

    stream_path = media.ensure_streamable(target)
    if stream_path is None:
        return JSONResponse(
            status_code=202,
            content={"detail": "Preparing video for browser playback."})
    if not stream_path.is_file():
        raise HTTPException(status_code=415,
                            detail="Video could not be prepared for playback.")

    return _stream_response(stream_path, request)



@app.get("/api/jobs/{job_id}/download-zip")
def download_zip(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job is not completed")
    zip_path = Path(job["zip_path"]) if job.get("zip_path") else None
    if not zip_path or not zip_path.is_file():
        raise HTTPException(status_code=404, detail="ZIP archive not found")
    stem = Path(job["filename"] or "conversion").stem[:60] or "conversion"
    return FileResponse(path=zip_path, filename=f"{stem}_converted.zip",
                        media_type="application/zip")


@app.get("/api/connectors")
def list_connectors():
    return registry.describe()


@app.get("/api/search", response_model=SearchResponse)
def search_platform_videos(q: str = Query(..., max_length=1000),
                          source: SearchSource = "all",
                          limit: int = Query(12, ge=1, le=24)):
    query = q.strip()
    if not 1 <= len(query) <= 200:
        raise HTTPException(status_code=400, detail="Search must contain 1 to 200 characters.")
    try:
        return search.search_videos(query, source, limit)
    except search.SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/resolve")
def resolve_urls(request: ResolveRequest):
    urls = [u.strip() for u in request.urls if isinstance(u, str) and u.strip()]
    return [{"url": u,
             "connector": (c.describe() if (c := registry.resolve(u)) else None)}
            for u in urls]


@app.post("/api/media")
def start_downloads(request: DownloadRequest):
    urls = []
    for u in request.urls:
        if isinstance(u, str) and u.strip() and u.strip() not in urls:
            urls.append(u.strip())
    if not urls:
        raise HTTPException(status_code=400, detail="At least one URL is required.")
    unsupported = [u for u in urls if registry.resolve(u) is None]
    if unsupported:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported URL(s): {', '.join(unsupported)}")
    return [library.video_payload(library.start_download(u, request.quality))
            for u in urls]


@app.get("/api/media")
def list_media(status: str | None = None):
    return [library.video_payload(row) for row in db.list_videos(status)]


@app.get("/api/media/{video_id}")
def get_media(video_id: str):
    row = db.get_video(video_id)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    return library.video_payload(row)


@app.delete("/api/media/{video_id}")
def delete_media(video_id: str):
    if not db.get_video(video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    library.cancel_and_delete(video_id)
    return {"id": video_id, "deleted": True}


@app.get("/api/media/{video_id}/stream")
def stream_media(video_id: str, request: Request):
    row = db.get_video(video_id)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    file_path = Path(row["file_path"]) if row.get("file_path") else None
    if row["status"] != "ready" or not file_path or not file_path.is_file():
        raise HTTPException(status_code=409, detail="Video is not ready")

    return _stream_response(file_path, request)


def _stream_response(file_path: Path, request: Request):
    size = file_path.stat().st_size
    start, end, status_code = media.parse_range(request.headers.get("range"), size)
    headers = {"Accept-Ranges": "bytes"}
    if status_code == 416:
        headers["Content-Range"] = f"bytes */{size}"
        return Response(status_code=416, headers=headers)
    length = end - start + 1
    headers["Content-Length"] = str(length)
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    media_type = ("video/webm" if file_path.suffix.lower() == ".webm" else
                  mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
    return StreamingResponse(
        media.iter_file(file_path, start, length),
        status_code=status_code, headers=headers, media_type=media_type)



@app.get("/api/media/{video_id}/thumbnail")
def media_thumbnail(video_id: str):
    row = db.get_video(video_id)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    thumb = Path(row["thumbnail_path"]) if row.get("thumbnail_path") else None
    if not thumb or not thumb.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    media_type = mimetypes.guess_type(thumb.name)[0] or "image/jpeg"
    return FileResponse(path=thumb, media_type=media_type)


@app.get("/api/media/{video_id}/download")
def download_media(video_id: str):
    row = db.get_video(video_id)
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    file_path = Path(row["file_path"]) if row.get("file_path") else None
    if row["status"] != "ready" or not file_path or not file_path.is_file():
        raise HTTPException(status_code=409, detail="Video is not ready")
    safe_title = re.sub(r"\s+", " ",
                        re.sub(r"[^\w\s.-]", "", row.get("title") or "")
                        ).strip()[:80] or "video"
    filename = f"{safe_title}{file_path.suffix}"
    media_type = ("video/webm" if file_path.suffix.lower() == ".webm" else
                  mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
    return FileResponse(path=file_path, filename=filename,
                        media_type=media_type)
