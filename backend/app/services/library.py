"""Persistent video library downloads via connectors."""
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..connectors import ConnectorCancelled, ConnectorError, registry
from ..utils import temp_manager
from . import db, media

_ACTIVE: dict[str, dict] = {}
_LOCK = threading.Lock()
_SLOTS = threading.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)


def start_download(url: str, quality: str = "best") -> dict:
    connector = registry.resolve(url)
    if connector is None:
        raise ValueError("No connector for URL")
    # Queued jobs keep the choices made when they were added to the library.
    settings = dict(db.get_download_settings())
    video_id = str(uuid.uuid4())
    media_dir = temp_manager.library_root() / video_id
    media_dir.mkdir(parents=True, exist_ok=True)
    row = db.create_video(video_id, url, connector.id, quality, media_dir)

    cancel = threading.Event()
    thread = threading.Thread(
        target=_run,
        args=(video_id, url, quality, connector, media_dir, cancel, settings),
        daemon=True)
    with _LOCK:
        _ACTIVE[video_id] = {"cancel": cancel, "thread": thread}
    thread.start()
    return row


def cancel_and_delete(video_id: str) -> None:
    with _LOCK:
        entry = _ACTIVE.get(video_id)
    if entry:
        entry["cancel"].set()
        entry["thread"].join(timeout=2)
    row = db.get_video(video_id)
    if row and row.get("media_dir"):
        media_dir = Path(row["media_dir"])
        for _ in range(10):
            shutil.rmtree(media_dir, ignore_errors=True)
            if not media_dir.exists():
                break
            threading.Event().wait(0.2)
    db.delete_video(video_id)


def video_payload(row: dict) -> dict:
    payload = {k: v for k, v in row.items()
               if k not in ("media_dir", "file_path", "thumbnail_path")}
    video_id = row["id"]
    payload["stream_url"] = f"/api/media/{video_id}/stream"
    payload["thumbnail_url"] = (f"/api/media/{video_id}/thumbnail"
                                if row.get("thumbnail_path") else None)
    payload["download_url"] = f"/api/media/{video_id}/download"
    file_path = row.get("file_path")
    payload["file_name"] = Path(file_path).name if file_path else None
    return payload


def wait_for(video_id: str, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    row = db.get_video(video_id)
    while time.time() < deadline:
        if row is None or row["status"] in ("ready", "failed"):
            return row
        time.sleep(0.1)
        row = db.get_video(video_id)
    return row


def _run(video_id: str, url: str, quality: str, connector,
         media_dir: Path, cancel: threading.Event, settings: dict) -> None:
    acquired = False
    try:
        while not cancel.is_set():
            acquired = _SLOTS.acquire(timeout=0.2)
            if acquired:
                break
        if cancel.is_set():
            return
        db.update_video(video_id, status="downloading", progress=0,
                        stage="downloading")
        reported = {"pct": -1, "stage": None}

        def on_progress(percent: float, stage: str) -> None:
            if cancel.is_set():
                raise ConnectorCancelled("Download cancelled.")
            pct = int(percent)
            if pct <= reported["pct"] and stage == reported["stage"]:
                return
            reported["pct"] = max(reported["pct"], pct)
            reported["stage"] = stage
            db.update_video(video_id, progress=reported["pct"], stage=stage)

        result = connector.download(url, media_dir, quality,
                                    on_progress, cancel)
        if cancel.is_set():
            return
        db.update_video(video_id, status="processing", progress=90,
                        stage="checking", title=result.info.title,
                        uploader=result.info.uploader, duration=result.info.duration)

        path = result.path
        playback_warning = None
        browser_compatible = media.is_browser_compatible(path)
        if settings["convert_for_browser"] and (
                path.suffix.lower() != ".mp4" or not browser_compatible):
            # MP4 is an explicit output choice, including for playable WebM files.
            db.update_video(video_id, stage="converting")
            target = path.with_name(path.stem + ".playback.mp4")
            if not media.convert_to_mp4(path, target, cancel):
                raise ConnectorError("Video could not be converted to MP4.")
            path.unlink(missing_ok=True)
            path = target
        elif not browser_compatible:
            playback_warning = (
                "Automatic conversion is off. This format may not play in "
                "your browser; download the original file to use an external player.")

        thumbnail = result.thumbnail
        if not (thumbnail and thumbnail.is_file()):
            thumbnail = None
            if settings["generate_thumbnails"]:
                db.update_video(video_id, stage="thumbnail")
                candidate = media_dir / "thumbnail.jpg"
                thumbnail = candidate if media.make_thumbnail(
                    path, candidate) else None

        if cancel.is_set():
            return
        db.update_video(
            video_id, status="ready", progress=100, stage=None,
            file_path=str(path), file_size=path.stat().st_size,
            thumbnail_path=str(thumbnail) if thumbnail else None,
            playback_warning=playback_warning,
            title=result.info.title, uploader=result.info.uploader,
            description=result.info.description,
            duration=result.info.duration, width=result.info.width,
            height=result.info.height,
            completed_at=datetime.now(timezone.utc).isoformat())
    except ConnectorCancelled:
        pass
    except Exception as exc:
        if not cancel.is_set():
            db.update_video(video_id, status="failed", stage=None,
                            error_message=str(exc)[:2000],
                            completed_at=datetime.now(timezone.utc).isoformat())
    finally:
        if acquired:
            _SLOTS.release()
        if cancel.is_set():
            shutil.rmtree(media_dir, ignore_errors=True)
        with _LOCK:
            _ACTIVE.pop(video_id, None)
