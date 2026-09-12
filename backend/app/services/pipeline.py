"""Adapter layer invoking original-project/download_rumble.py per job."""
import json
import os
import signal
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..utils import temp_manager
from . import db

_ACTIVE = {}
_LOCK = threading.Lock()

_VALUE_OPTIONS = {
    "model": "--model",
    "language": "--language",
    "device": "--device",
    "compute_type": "--compute-type",
    "workers": "--workers",
    "chunk_seconds": "--chunk-seconds",
    "impersonate": "--impersonate",
}

_FLAG_OPTIONS = {
    "keep_video": "--keep-video",
    "allow_video_fallback": "--allow-video-fallback",
    "audio_only": "--audio-only",
    "no_transcript": "--no-transcript",
    "overwrite_transcript": "--overwrite-transcript",
    "no_vad": "--no-vad",
    "verbose": "--verbose",
}

_STAGE_PROGRESS = [
    ("Streaming format", 15),
    ("Checkpoint:", 15),
    ("Using cached transcript", 50),
    ("Using subtitle track", 55),
    ("Falling back to local speech recognition", 20),
    ("Loading OpenAI Whisper", 25),
    ("Transcription chunk checkpoint saved", None),
    ("Transcribed chunk", None),
    ("Transcript saved", 88),
    ("Audio saved", 92),
    ("Downloading and preserving video", 95),
]


def build_command(job_dir: Path, params: dict) -> list:
    urls_file = job_dir / "inputs" / "urls.json"
    cmd = [sys.executable, str(config.DOWNLOADER_SCRIPT),
           "--url-file", str(urls_file),
           "-o", str(job_dir / "outputs")]
    for key, flag in _VALUE_OPTIONS.items():
        value = params.get(key)
        if value is not None:
            cmd += [flag, str(value)]
    gpu_indices = params.get("gpu_indices")
    if gpu_indices:
        cmd += ["--gpu-indices", *[str(i) for i in gpu_indices]]
    for key, flag in _FLAG_OPTIONS.items():
        if params.get(key):
            cmd.append(flag)
    return cmd


def package_outputs(job_dir: Path) -> Path:
    outputs = job_dir / "outputs"
    archive = job_dir / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    zip_path = archive / "conversion_output.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(outputs.rglob("*")):
            if file.is_file() and not any(
                    part.startswith(".") for part in file.relative_to(outputs).parts):
                zf.write(file, arcname=file.relative_to(outputs))
        log = job_dir / "work" / "job.log"
        if log.is_file():
            zf.write(log, arcname="job.log")
    return zip_path


def _progress_for_line(line: str, current: int) -> int:
    for marker, value in _STAGE_PROGRESS:
        if marker in line:
            if value is None:
                return min(85, current + 4)
            return max(current, value)
    return current


def _terminate(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def discard_job(job_id: str) -> None:
    with _LOCK:
        proc = _ACTIVE.pop(job_id, None)
    if proc and proc.poll() is None:
        _terminate(proc)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    job = db.get_job(job_id)
    if job:
        job_dir = Path(job["temp_dir"])
        for _ in range(10):
            temp_manager.purge_job_dir(job_dir)
            if not job_dir.exists():
                break
            threading.Event().wait(0.2)
    db.update_job(job_id, status="discarded", progress=0,
                  completed_at=datetime.now(timezone.utc).isoformat())


def run_job(job_id: str) -> None:
    job = db.get_job(job_id)
    if not job:
        return
    job_dir = Path(job["temp_dir"])
    params = job.get("params") or {}
    log_path = job_dir / "work" / "job.log"
    db.update_job(job_id, status="in_progress", progress=5, log_path=str(log_path))

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        build_command(job_dir, params),
        cwd=str(config.ORIGINAL_PROJECT_DIR),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        **kwargs,
    )
    with _LOCK:
        _ACTIVE[job_id] = proc

    progress = 5
    tail = []
    try:
        with log_path.open("w", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                tail.append(line.rstrip())
                del tail[:-40]
                updated = _progress_for_line(line, progress)
                if updated != progress:
                    progress = updated
                    db.update_job(job_id, progress=progress)
        returncode = proc.wait()
    finally:
        with _LOCK:
            _ACTIVE.pop(job_id, None)

    current = db.get_job(job_id)
    if not current or current["status"] == "discarded":
        return

    if returncode == 0:
        zip_path = package_outputs(job_dir)
        db.update_job(job_id, status="completed", progress=100,
                      zip_path=str(zip_path),
                      completed_at=datetime.now(timezone.utc).isoformat())
    else:
        db.update_job(job_id, status="failed",
                      error_message="\n".join(tail[-10:]) or f"Exited with code {returncode}",
                      completed_at=datetime.now(timezone.utc).isoformat())


def start_job_thread(job_id: str) -> threading.Thread:
    thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    thread.start()
    return thread
