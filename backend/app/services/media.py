"""Video discovery and browser-playable stream preparation."""
import hashlib
import json
import re
import subprocess
import threading
from pathlib import Path

from . import db

VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi"}
PLAYABLE_EXTS = {".mp4", ".m4v", ".webm", ".mov"}

# Local uploads are media files, never manifests that can open other files or
# network URLs. Restrict demuxers as well as protocols before probing/decoding.
_LOCAL_FORMATS = ("mov,matroska,webm,avi,mp3,wav,aiff,flac,ogg,aac,ac3,eac3,"
                  "asf,mpeg,mpegts,flv,rm,amr,ape,wv,tta,mpc,mpc8,au,voc,caf,nut,"
                  "h264,hevc,m4v,obu,ivf,loas,dts,dtshd,shorten,tak,dsf,iff,swf")
_IMAGE_FORMATS = "jpeg_pipe,png_pipe,webp_pipe,bmp_pipe,tiff_pipe,gif"


def _local_input(path: Path, formats: str = _LOCAL_FORMATS) -> list[str]:
    return ["-protocol_whitelist", "file,pipe", "-format_whitelist", formats,
            "-i", str(path)]


def probe_upload(path: Path, *, thumbnail: bool = False) -> dict:
    """Validate uploaded content without trusting its filename or MIME type."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-max_alloc", "134217728",
         *_local_input(path, _IMAGE_FORMATS if thumbnail else _LOCAL_FORMATS),
         "-show_streams", "-show_format", "-of", "json"],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("The file could not be read as a supported "
                         + ("image." if thumbnail else "audio or video file."))
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not any(s.get("codec_type") in ("audio", "video") for s in streams):
        raise ValueError("The uploaded file does not contain audio or video.")
    if thumbnail:
        image = next((s for s in streams if s.get("codec_type") == "video"), {})
        width, height = image.get("width", 0), image.get("height", 0)
        if not width or not height or max(width, height) > 12000 or width * height > 40000000:
            raise ValueError("Thumbnail must be an image of at most 40 megapixels and 12000 pixels per side.")
    return data


def normalize_thumbnail(source: Path, target: Path) -> None:
    """Decode one bounded-size bitmap and write sanitized JPEG artwork."""
    probe_upload(source, thumbnail=True)
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-max_alloc", "134217728", *_local_input(source, _IMAGE_FORMATS),
             "-map", "0:v:0", "-frames:v", "1", "-an", "-map_metadata", "-1",
             "-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease",
             "-pix_fmt", "yuvj420p", "-update", "1", str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or not target.is_file() or not target.stat().st_size:
            raise ValueError("The thumbnail could not be decoded as an image.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise

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
    if video.is_file() and not db.get_download_settings()["convert_for_browser"]:
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
    if not db.get_download_settings()["convert_for_browser"]:
        return video if video.is_file() else None
    with _LOCK:
        if stream_path in _PREPARING:
            return None
        _PREPARING.add(stream_path)
    threading.Thread(target=_prepare, args=(video, stream_path), daemon=True).start()
    return None


def probe_streams(video: Path) -> list[dict]:
    """Read actual codecs; a container extension alone does not prove playback."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("Downloaded file could not be read as a video.")
    streams = json.loads(result.stdout).get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise ValueError("The source did not contain a video track.")
    return streams


def _mp4_codecs_supported(streams: list[dict]) -> bool:
    video = next(s for s in streams if s.get("codec_type") == "video")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    return (video.get("codec_name") == "h264"
            and video.get("pix_fmt") in ("yuv420p", "yuvj420p")
            and all(s.get("codec_name") in ("aac", "mp3") for s in audio))


def is_browser_compatible(video: Path) -> bool:
    """Recognize native browser formats; actual decoder support is browser-specific."""
    streams = probe_streams(video)
    if video.suffix.lower() in (".mp4", ".m4v"):
        return _mp4_codecs_supported(streams)
    if video.suffix.lower() == ".webm":
        return all(
            s.get("codec_name") in ("vp8", "vp9", "av1")
            if s.get("codec_type") == "video"
            else s.get("codec_name") in ("opus", "vorbis")
            for s in streams if s.get("codec_type") in ("video", "audio"))
    return False


def _run_conversion(command: list[str], cancel=None) -> bool:
    with subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        while True:
            if cancel is not None and cancel.is_set():
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise InterruptedError("Video preparation cancelled.")
            try:
                process.communicate(timeout=0.25)
                return process.returncode == 0
            except subprocess.TimeoutExpired:
                continue


def convert_to_mp4(video: Path, target: Path, cancel=None) -> bool:
    """Remux compatible tracks, otherwise produce H.264/AAC for the players."""
    target.parent.mkdir(parents=True, exist_ok=True)
    streams = probe_streams(video)
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            *_local_input(video), "-map", "0:v:0", "-map", "0:a:0?"]
    if _mp4_codecs_supported(streams):
        if _run_conversion(base + ["-c", "copy", "-movflags", "+faststart",
                                   str(target)], cancel):
            return True
        target.unlink(missing_ok=True)
    ok = _run_conversion(
        base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", str(target)], cancel)
    if not ok:
        target.unlink(missing_ok=True)
    return ok


def convert_audio_to_mp4(audio: Path, thumbnail: Path, target: Path, cancel=None) -> bool:
    """Render an audio track as H.264/AAC MP4 using one still image."""
    target.parent.mkdir(parents=True, exist_ok=True)
    ok = _run_conversion(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-stream_loop", "-1", *_local_input(thumbnail, _IMAGE_FORMATS),
         *_local_input(audio), "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
         "-vf", "scale='min(1280,iw)':-2,format=yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest",
         "-movflags", "+faststart", str(target)], cancel)
    if not ok:
        target.unlink(missing_ok=True)
    return ok


def convert_to_mp3(video: Path, target: Path, cancel=None) -> bool:
    """Extract the first audio track into a broadly compatible MP3 file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    ok = _run_conversion(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         *_local_input(video), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame",
         "-q:a", "2", str(target)], cancel)
    if not ok:
        target.unlink(missing_ok=True)
    return ok


def make_thumbnail(video: Path, target: Path) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    for seek in ("1", "0"):
        try:
            ok = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-ss", seek, *_local_input(video), "-frames:v", "1",
                 "-vf", "scale=480:-2", "-pix_fmt", "yuvj420p", str(target)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            ok = False
        if ok and target.is_file():
            return True
        try:
            target.unlink(missing_ok=True)
        except OSError:
            return False
    return False


def _prepare(video: Path, stream_path: Path) -> None:
    staged = stream_path.with_name(stream_path.stem + ".tmp.mp4")
    try:
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        ok = convert_to_mp4(video, staged)
        if ok:
            staged.replace(stream_path)
        else:
            staged.unlink(missing_ok=True)
    finally:
        with _LOCK:
            _PREPARING.discard(stream_path)


def parse_range(range_header: str | None, size: int):
    """Return (start, end, status), with 416 for unsatisfiable byte ranges.

    Malformed or multi-range requests are ignored, as permitted by HTTP.
    """
    if range_header:
        match = _RANGE_RE.fullmatch(range_header.strip())
        if match:
            start_s, end_s = match.groups()
            if start_s or end_s:
                if start_s:
                    start = int(start_s)
                    end = min(int(end_s), size - 1) if end_s else size - 1
                    if start >= size or start > end:
                        return 0, -1, 416
                else:
                    suffix = int(end_s)
                    if suffix == 0 or size == 0:
                        return 0, -1, 416
                    start, end = max(0, size - suffix), size - 1
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
