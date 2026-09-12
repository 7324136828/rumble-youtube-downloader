"""FastAPI entrypoint and route declarations."""
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)

from . import config
from .schemas.job import ConvertRequest
from .services import db, media, pipeline
from .utils import temp_manager

app = FastAPI(title="Rumble/YouTube Conversion API")

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
    if not config.DOWNLOADER_SCRIPT.is_file():
        raise RuntimeError(
            f"original-project script not found: {config.DOWNLOADER_SCRIPT}")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/convert")
def start_conversion(request: ConvertRequest):
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
    if not str(target).startswith(str(base)) or not target.is_file():
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
    if (not str(target).startswith(str(outputs))
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

    size = stream_path.stat().st_size
    start, end, status = media.parse_range(request.headers.get("range"), size)
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Range": f"bytes {start}-{end}/{size}",
    }
    media_type = mimetypes.guess_type(stream_path.name)[0] or "video/mp4"
    return StreamingResponse(
        media.iter_file(stream_path, start, length),
        status_code=status, headers=headers, media_type=media_type)


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
