"""Video discovery and browser-playable stream preparation."""
import hashlib
import re
import subprocess
import threading
from pathlib import Path

VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi"}
PLAYABLE_EXTS = {".mp4", ".m4v", ".webm", ".mov"}

_PREPARING = set()
_LOCK = threading.Lock()
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def find_videos(job_dir: Path) -> list:
    outputs = Path(job_dir) / "outputs"
    if not outputs.is_dir():
        return []
    return sorted(
        p for p in outputs.rglob("*")
        if p.is_file()
        and p.suffix.lower() in VIDEO_EXTS
        and not any(part.startswith(".") for part in p.relative_to(outputs).parts)
    )


def stream_path_for(video: Path) -> Path:
    if video.suffix.lower() in PLAYABLE_EXTS:
        return video
    job_dir = video.parent
    while job_dir.name != "outputs" and job_dir.parent != job_dir:
        job_dir = job_dir.parent
    work = job_dir.parent / "work"
    digest = hashlib.sha1(str(video).encode()).hexdigest()[:8]
    return work / f"{video.stem}-{digest}.stream.mp4"


def video_state(job_dir: Path, video: Path) -> str:
    if stream_path_for(video).is_file():
        return "ready"
    with _LOCK:
        if stream_path_for(video) in _PREPARING:
            return "preparing"
    return "pending"


def ensure_streamable(video: Path):
    """Return a browser-playable path, or None while preparation runs."""
    stream_path = stream_path_for(video)
    if stream_path.is_file():
        return stream_path
    if video.suffix.lower() in PLAYABLE_EXTS:
        return video if video.is_file() else None
    with _LOCK:
        if stream_path in _PREPARING:
            return None
        _PREPARING.add(stream_path)
    threading.Thread(target=_prepare, args=(video, stream_path), daemon=True).start()
    return None


def _prepare(video: Path, stream_path: Path) -> None:
    staged = stream_path.with_name(stream_path.stem + ".tmp.mp4")
    try:
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        ok = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(video), "-c", "copy", "-strict", "experimental",
             "-movflags", "+faststart", str(staged)],
        ).returncode == 0
        if not ok:
            staged.unlink(missing_ok=True)
            ok = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(video), "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "23", "-c:a", "aac", "-movflags", "+faststart",
                 str(staged)],
            ).returncode == 0
        if ok:
            staged.replace(stream_path)
        else:
            staged.unlink(missing_ok=True)
    finally:
        with _LOCK:
            _PREPARING.discard(stream_path)


def parse_range(range_header: str | None, size: int):
    """Return (start, end, status) for a Range request."""
    if range_header:
        match = _RANGE_RE.match(range_header.strip())
        if match:
            start_s, end_s = match.groups()
            if start_s or end_s:
                if start_s:
                    start = int(start_s)
                    end = int(end_s) if end_s else size - 1
                else:
                    start = max(0, size - int(end_s))
                    end = size - 1
                if start <= end < size:
                    return start, end, 206
    return 0, size - 1, 200


def iter_file(path: Path, start: int, length: int, chunk_size: int = 1024 * 1024):
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
