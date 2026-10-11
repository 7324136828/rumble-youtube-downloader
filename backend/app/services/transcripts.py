"""Source subtitles with local Whisper fallback for saved library media."""
from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..connectors.cookies import read_cookie_file
from ..utils import transcribe
from ..utils.runtime import ytdlp_command
from . import db, media

_LOG = logging.getLogger(__name__)
_LOCK = threading.RLock()
_JOBS: dict[str, dict] = {}
_SLOTS = threading.Semaphore(1)
_ACTIVE_STATES = {"queued", "fetching", "transcribing"}
_MAX_TEXT_BYTES = 20 * 1024 * 1024
_TIMING = re.compile(r"^((?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
                     r"((?:\d+:)?\d{2}:\d{2}[.,]\d{3})")
_CAPTION_TAG = re.compile(r"</?[A-Za-z][^>]*>|<(?:\d+:)?\d{2}:\d{2}\.\d{3}>")


def _checked_file(video: dict, row: dict | None) -> Path | None:
    if not row or row.get("status") != "completed" or not row.get("text_path") or not video.get("media_dir"):
        return None
    path = Path(row["text_path"])
    try:
        valid = (path.is_file() and path.resolve().is_relative_to(Path(video["media_dir"]).resolve())
                 and path.stat().st_size <= _MAX_TEXT_BYTES)
    except OSError:
        valid = False
    if not valid:
        return None
    return path


def transcript_payload(video_id: str, include_text: bool = False) -> dict:
    video = db.get_video(video_id)
    if not video:
        raise LookupError("Video not found")
    row = db.get_video_transcript(video_id)
    status = row["status"] if row else "idle"
    path = _checked_file(video, row)
    error = row.get("error_message") if row else None
    if status == "completed" and path is None:
        status, error = "failed", "The saved transcript is missing; get the transcript again."
    base = f"/api/media/{video_id}/transcript"
    payload = {"status": status, "source": row.get("source") if row else None,
               "language": row.get("language") if row else None, "error_message": error,
               "text_url": base if path else None,
               "download_url": f"{base}/download" if path else None}
    if include_text:
        payload["text"] = path.read_text(encoding="utf-8") if path else None
    return payload


def transcript_file(video_id: str) -> Path:
    video = db.get_video(video_id)
    if not video:
        raise LookupError("Video not found")
    path = _checked_file(video, db.get_video_transcript(video_id))
    if path is None:
        raise RuntimeError("The transcript is not ready yet")
    return path


def start_transcript(video_id: str) -> dict:
    # Deletion holds the same edit lock while cancelling workers and purging
    # files. Import lazily because library also uses transcript payloads.
    from . import library
    with library._MEDIA_EDIT_LOCK, _LOCK:
        if video_id in library._DELETING:
            raise LookupError("Video not found")
        video = db.get_video(video_id)
        if not video:
            raise LookupError("Video not found")
        source = Path(video["file_path"]) if video.get("file_path") else None
        if video["status"] != "ready" or not source or not source.is_file() or not video.get("media_dir"):
            raise RuntimeError("Download this media before requesting its transcript")
        if not source.resolve().is_relative_to(Path(video["media_dir"]).resolve()):
            raise ValueError("Media file is outside its library directory")
        if video_id in _JOBS or _checked_file(video, db.get_video_transcript(video_id)):
            return transcript_payload(video_id)
        db.upsert_video_transcript(video_id, status="queued")
        cancel = threading.Event()
        thread = threading.Thread(target=_run, args=(video_id, video, cancel), daemon=True)
        _JOBS[video_id] = {"cancel": cancel, "thread": thread, "process": None}
        thread.start()
        return transcript_payload(video_id)


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
            if result.returncode and process.poll() is None:
                process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()


def cancel_transcript(video_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(video_id)
        if job:
            job["cancel"].set()
            process = job.get("process")
        else:
            process = None
    if process:
        _terminate(process)
    if job and job["thread"] is not threading.current_thread():
        job["thread"].join(timeout=3)


def shutdown() -> None:
    with _LOCK:
        ids = list(_JOBS)
    for video_id in ids:
        cancel_transcript(video_id)


def _check_cancel(video_id: str, cancel: threading.Event) -> None:
    if cancel.is_set() or db.get_video(video_id) is None:
        raise InterruptedError("Transcript cancelled")


def _command(video_id: str, cancel: threading.Event, command: list[str], timeout: float) -> str:
    _check_cancel(video_id, cancel)
    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
              if os.name == "nt" else {"start_new_session": True})
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", **kwargs)
    with _LOCK:
        if video_id in _JOBS:
            _JOBS[video_id]["process"] = process
    deadline = time.monotonic() + timeout
    try:
        while True:
            _check_cancel(video_id, cancel)
            if time.monotonic() > deadline:
                raise TimeoutError("Transcript processing timed out; retry this video")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            # Subtitle failures fall back to Whisper. Only the local Whisper
            # command's message is exposed by its caller, never cookie errors.
            raise RuntimeError((stdout.strip() or stderr.strip() or "Transcript command failed")[-2000:])
        return stdout
    finally:
        if process.poll() is None:
            _terminate(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        with _LOCK:
            if video_id in _JOBS and _JOBS[video_id].get("process") is process:
                _JOBS[video_id]["process"] = None


def select_subtitle_language(info: dict) -> str | None:
    preferred = str(info.get("language") or "en").lower()
    for tracks in (info.get("subtitles") or {}, info.get("automatic_captions") or {}):
        languages = [key for key, value in tracks.items() if value and key != "live_chat"]
        if not languages:
            continue
        def rank(language: str) -> tuple:
            lower = language.lower()
            return (0 if lower == preferred else 1 if lower.split("-")[0] == preferred.split("-")[0]
                    else 2 if lower.endswith("-orig") else 3 if lower == "en" else 4, language)
        return min(languages, key=rank)
    return None


def _seconds(value: str) -> float:
    total = 0.0
    for part in value.replace(",", ".").split(":"):
        total = total * 60 + float(part)
    return total


def _join_captions(captions: list[tuple[str, float | None, float | None]], rolling: bool) -> str:
    words: list[str] = []
    previous_end = None
    for caption, start, end in captions:
        incoming = caption.split()
        if not incoming:
            continue
        # Only overlapping automatic cues repeat a display window. Independent
        # cues may contain real repeated speech, which must be kept intact.
        overlap = 0
        if rolling and start is not None and previous_end is not None and start < previous_end:
            for size in range(min(len(words), len(incoming), 100), 0, -1):
                if words[-size:] == incoming[:size]:
                    overlap = size
                    break
        words.extend(incoming[overlap:])
        previous_end = end
    return " ".join(words).strip()


def subtitle_text(path: Path, *, rolling: bool = False) -> str:
    if path.stat().st_size > _MAX_TEXT_BYTES:
        raise ValueError("The source subtitle file is too large")
    raw = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".json3":
        data = json.loads(raw)
        captions = [("".join(str(segment.get("utf8") or "") for segment in event.get("segs", [])),
                     event.get("tStartMs", 0) / 1000,
                     (event.get("tStartMs", 0) + event.get("dDurationMs", 0)) / 1000)
                    for event in data.get("events", [])]
    elif suffix in {".ttml", ".srv1", ".srv2", ".srv3", ".xml", ".dfxp"}:
        tree = ET.fromstring(raw)
        for node in tree.iter():
            if node.tag.rsplit("}", 1)[-1] == "br":
                node.text = " "
        captions = [("".join(node.itertext()), None, None) for node in tree.iter()
                    if node.tag.rsplit("}", 1)[-1] in {"p", "text"}]
    else:
        captions, current = [], []
        in_cue = False
        start, end = None, None
        def cue_text() -> str:
            return html.unescape(_CAPTION_TAG.sub("", " ".join(current)))
        for line in raw.splitlines():
            line = line.strip()
            timing = _TIMING.match(line)
            if timing:
                if current:
                    captions.append((cue_text(), start, end))
                current, in_cue = [], True
                start, end = _seconds(timing[1]), _seconds(timing[2])
            elif not line:
                if current:
                    captions.append((cue_text(), start, end))
                current, in_cue = [], False
            elif in_cue:
                current.append(line)
        if current:
            captions.append((cue_text(), start, end))
    return _join_captions(captions, rolling)


def _fetch_subtitles(video_id: str, video: dict, work: Path, cancel: threading.Event) -> tuple[str, str] | None:
    if video.get("connector") == "upload":
        return None
    settings = db.get_download_settings()
    command = [*ytdlp_command(), "--skip-download", "--no-playlist", "--quiet", "--no-warnings",
               "--socket-timeout", "20", "--retries", "1", "--restrict-filenames"]
    cookie_path = settings.get("cookie_file") or config.YTDLP_COOKIE_FILE
    if cookie_path:
        with read_cookie_file(Path(cookie_path)) as cookies:
            copied_cookies = work / "cookies.txt"
            copied_cookies.write_text(cookies.read(), encoding="utf-8")
        command += ["--cookies", str(copied_cookies)]
    elif settings.get("cookie_browser"):
        browser = settings["cookie_browser"]
        if settings.get("cookie_browser_profile"):
            browser += ":" + settings["cookie_browser_profile"]
        command += ["--cookies-from-browser", browser]
    if video.get("connector") == "rumble":
        command += ["--impersonate", "chrome"]
    source_url = video["source_url"]
    info = json.loads(_command(video_id, cancel, [*command, "--dump-single-json", "--", source_url], 90))
    language = select_subtitle_language(info)
    if not language:
        return None
    _command(video_id, cancel, [*command, "--write-subs", "--write-auto-subs", "--sub-langs",
                               "^" + re.escape(language) + "$", "--sub-format", "vtt/srt/json3/best",
                               "-o", str(work / "subtitle.%(ext)s"), "--", source_url], 180)
    for subtitle in sorted(work.glob("subtitle.*")):
        if subtitle.is_file() and subtitle.resolve().is_relative_to(work.resolve()):
            text = subtitle_text(subtitle, rolling=language not in (info.get("subtitles") or {}))
            if text:
                return text, language
    return None


def _generate_whisper(video_id: str, video: dict, work: Path, cancel: threading.Event) -> tuple[str, str | None]:
    audio = work / "audio.wav"
    _command(video_id, cancel,
             ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
              *media._local_input(Path(video["file_path"])), "-vn", "-ac", "1", "-ar", "16000",
              "-f", "wav", str(audio)], 1200)
    result = work / "whisper.json"
    prefix = ([sys.executable, "--whisper-transcript"] if getattr(sys, "frozen", False)
              else [sys.executable, str(Path(transcribe.__file__).resolve())])
    _command(video_id, cancel, [*prefix, "--input", str(audio), "--output", str(result),
                               "--model-dir", str(config.JOBS_ROOT / "models" / "whisper"),
                               "--model", os.environ.get("WHISPER_MODEL") or "base",
                               "--device", os.environ.get("WHISPER_DEVICE") or "cpu"], 6 * 60 * 60)
    if not result.is_file() or result.stat().st_size > _MAX_TEXT_BYTES:
        raise RuntimeError("Whisper did not produce a transcript")
    data = json.loads(result.read_text(encoding="utf-8"))
    text = (data.get("text") or "").strip()
    if not text:
        raise RuntimeError("Whisper did not detect any speech in this media")
    return text, data.get("language")


def _run(video_id: str, video: dict, cancel: threading.Event) -> None:
    acquired = False
    work = Path(video["media_dir"]) / ".transcript-work"
    staged = Path(video["media_dir"]) / ".transcript.partial.txt"
    try:
        while not cancel.is_set():
            acquired = _SLOTS.acquire(timeout=0.2)
            if acquired:
                break
        _check_cancel(video_id, cancel)
        media_dir = Path(video["media_dir"]).resolve()
        if work.resolve().parent != media_dir or staged.resolve().parent != media_dir:
            raise ValueError("Transcript working files are outside the media directory")
        work.mkdir(exist_ok=True)
        db.update_video_transcript(video_id, status="fetching")
        try:
            result = _fetch_subtitles(video_id, video, work, cancel)
        except InterruptedError:
            raise
        except Exception:
            # A source may have no captions, require verification, or be offline.
            # The saved media remains available for local speech recognition.
            _LOG.info("Source subtitles unavailable for %s; using local Whisper", video_id)
            result = None
        _check_cancel(video_id, cancel)
        source = "subtitles"
        if not result:
            db.update_video_transcript(video_id, status="transcribing")
            result = _generate_whisper(video_id, video, work, cancel)
            source = "whisper"
        text, language = result
        if not text.strip():
            raise RuntimeError("The transcript is empty")
        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise RuntimeError("The transcript is too large to save")
        staged.write_text(text.strip() + "\n", encoding="utf-8")
        with _LOCK:
            _check_cancel(video_id, cancel)
            target = Path(video["media_dir"]) / "transcript.txt"
            staged.replace(target)
            db.update_video_transcript(video_id, status="completed", text_path=str(target),
                                       source=source, language=language, error_message=None,
                                       completed_at=datetime.now(timezone.utc).isoformat())
    except InterruptedError:
        db.update_video_transcript(video_id, status="failed", error_message="Transcript interrupted; retry to continue.",
                                   completed_at=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        db.update_video_transcript(video_id, status="failed", error_message=str(error)[-2000:],
                                   completed_at=datetime.now(timezone.utc).isoformat())
    finally:
        try:
            if staged.resolve().parent == Path(video["media_dir"]).resolve():
                staged.unlink(missing_ok=True)
            # Explicit task directory, never remove media or the shared model cache.
            if work.resolve().parent == Path(video["media_dir"]).resolve():
                shutil.rmtree(work, ignore_errors=True)
        except OSError:
            _LOG.info("Transcript working files for %s could not be removed", video_id)
        finally:
            if acquired:
                _SLOTS.release()
            with _LOCK:
                _JOBS.pop(video_id, None)
