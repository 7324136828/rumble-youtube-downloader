"""Persistent downloads, local media uploads, and playable derivatives."""
import logging
import math
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..connectors import ConnectorCancelled, ConnectorError, registry
from ..utils import temp_manager
from . import db, media, video_keywords, watch_later_thumbnails

_ACTIVE: dict[str, dict] = {}
_CONVERSIONS: dict[tuple[str, str], dict] = {}
_LOCK = threading.Lock()
_MEDIA_EDIT_LOCK = threading.RLock()
_DELETING: set[str] = set()
_SLOTS = threading.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)
_CONVERSION_FORMATS = {"mp4", "mp3"}
_LOG = logging.getLogger(__name__)
RETENTION_INTERVAL_SECONDS = 60 * 60
_RETENTION_LOCK = threading.Lock()
_RETENTION_THREAD = None
_RETENTION_STOP = None


def _remove_downloaded_from_watch_later(url: str) -> bool:
    """Remove a successfully downloaded URL without risking its ready media row."""
    try:
        item = db.get_watch_later_by_source_url(url)
        if item is None:
            return False
        watch_later_thumbnails.remove_local(item["catalog_id"])
        return bool(db.remove_watch_later(item["catalog_id"])["removed"])
    except Exception:
        _LOG.exception("Could not remove a completed download from Watch later.")
        return False


def remove_downloaded_watch_later() -> int:
    """Reconcile saved entries against downloads completed before this process started."""
    return sum(_remove_downloaded_from_watch_later(row["source_url"])
               for row in db.list_videos(status="ready"))


def _retention_loop(stop, interval=RETENTION_INTERVAL_SECONDS) -> None:
    while not stop.wait(interval):
        try:
            purge_expired()
        except Exception:
            _LOG.exception("Scheduled expired-video cleanup failed; it will retry in one hour.")


def start_retention_scheduler() -> None:
    global _RETENTION_THREAD, _RETENTION_STOP
    with _RETENTION_LOCK:
        if _RETENTION_THREAD is not None and _RETENTION_THREAD.is_alive():
            return
        _RETENTION_STOP = threading.Event()
        _RETENTION_THREAD = threading.Thread(
            target=_retention_loop, args=(_RETENTION_STOP,),
            daemon=True, name="expired-video-cleanup")
        _RETENTION_THREAD.start()


def stop_retention_scheduler() -> None:
    global _RETENTION_THREAD, _RETENTION_STOP
    with _RETENTION_LOCK:
        thread, stop = _RETENTION_THREAD, _RETENTION_STOP
        _RETENTION_THREAD = _RETENTION_STOP = None
    if stop is not None:
        stop.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5)


def start_download(url: str, quality: str = "best") -> dict:
    connector = registry.resolve(url)
    if connector is None:
        raise ValueError("No connector for URL")
    # Queued jobs keep the choices made when they were added to the library.
    settings = dict(db.get_download_settings())
    video_id = str(uuid.uuid4())
    media_dir = temp_manager.library_root() / video_id
    media_dir.mkdir(parents=True, exist_ok=True)
    days = settings.get("retention_days", 7)
    retention_days = days if settings.get("auto_delete_enabled", days > 0) and days > 0 else None
    row = db.create_video(video_id, url, connector.id, quality, media_dir, retention_days)

    cancel = threading.Event()
    thread = threading.Thread(
        target=_run,
        args=(video_id, url, quality, connector, media_dir, cancel, settings),
        daemon=True)
    with _LOCK:
        _ACTIVE[video_id] = {"cancel": cancel, "thread": thread}
    thread.start()
    return row


class UploadTooLarge(ValueError):
    pass


def _copy_upload(upload, target: Path, maximum: int) -> None:
    size = 0
    with target.open("wb") as output:
        while chunk := upload.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise UploadTooLarge(f"The uploaded file exceeds the {maximum // (1024 ** 2)} MB limit.")
            output.write(chunk)
    if not size:
        raise ValueError("The uploaded file is empty.")


def _upload_extension(info: dict, filename: str) -> str:
    formats = set(info.get("format", {}).get("format_name", "").split(","))
    for names, suffix in (({"mov", "mp4"}, ".mp4"), ({"mp3"}, ".mp3"),
                           ({"wav"}, ".wav"), ({"flac"}, ".flac"),
                           ({"ogg"}, ".ogg"), ({"aac"}, ".aac"),
                           ({"avi"}, ".avi"), ({"asf"}, ".asf"),
                           ({"mpegts"}, ".ts"), ({"mpeg"}, ".mpg"),
                           ({"flv"}, ".flv"), ({"aiff"}, ".aiff")):
        if formats & names:
            return suffix
    if formats & {"matroska", "webm"}:
        codecs = {stream.get("codec_name") for stream in info["streams"]
                  if stream.get("codec_type") in ("audio", "video")}
        return ".webm" if codecs <= {"vp8", "vp9", "av1", "opus", "vorbis"} else ".mkv"
    suffix = Path(filename.replace("\\", "/")).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".media"


def start_upload(upload, filename: str, title: str | None = None, thumbnail=None) -> dict:
    """Persist and validate multipart streams, then prepare local playback in a worker."""
    settings = dict(db.get_download_settings())
    video_id = str(uuid.uuid4())
    media_dir = temp_manager.library_root() / video_id
    media_dir.mkdir()
    source = media_dir / "original.upload"
    try:
        _copy_upload(upload, source, config.MAX_UPLOAD_BYTES)
        info = media.probe_upload(source)
        video_stream = next((s for s in info["streams"] if s.get("codec_type") == "video"
                             and not s.get("disposition", {}).get("attached_pic")), None)
        has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
        if video_stream is None and not has_audio:
            raise ValueError("The uploaded file does not contain a playable audio or video track.")
        media_kind = "video" if video_stream is not None else "audio"
        renamed = source.with_suffix(_upload_extension(info, filename))
        source.rename(renamed)
        source = renamed
        thumbnail_path = None
        if thumbnail is not None:
            uploaded_image = media_dir / ".thumbnail.upload"
            _copy_upload(thumbnail, uploaded_image, config.MAX_THUMBNAIL_BYTES)
            thumbnail_path = media_dir / f"custom-{uuid.uuid4().hex}.jpg"
            media.normalize_thumbnail(uploaded_image, thumbnail_path)
            uploaded_image.unlink()
        days = settings.get("retention_days", 7)
        retention_days = days if settings.get("auto_delete_enabled", days > 0) and days > 0 else None
        duration_value = info.get("format", {}).get("duration")
        try:
            duration = float(duration_value)
            if not math.isfinite(duration) or duration < 0:
                duration = None
        except (TypeError, ValueError):
            duration = None
        default_title = Path(filename.replace("\\", "/")).stem or "Uploaded media"
        cancel = threading.Event()
        thread = threading.Thread(target=_run_upload,
                                  args=(video_id, source, media_dir, media_kind,
                                        thumbnail_path, settings, cancel), daemon=True)
        with _LOCK:
            # Make the row and its cancellation handle visible together, so an
            # immediate Delete from another client cannot miss the new worker.
            db.create_video(video_id, f"upload:{video_id}", "upload", "original", media_dir, retention_days)
            db.update_video(video_id, title=(title or "").strip()[:500] or default_title[:500],
                            file_path=str(source), file_size=source.stat().st_size,
                            thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
                            media_kind=media_kind, duration=duration,
                            width=video_stream.get("width") if video_stream else None,
                            height=video_stream.get("height") if video_stream else None)
            row = db.get_video(video_id)
            _ACTIVE[video_id] = {"cancel": cancel, "thread": thread}
            thread.start()
        return row
    except BaseException:
        shutil.rmtree(media_dir, ignore_errors=True)
        db.delete_video(video_id)
        with _LOCK:
            _ACTIVE.pop(video_id, None)
        raise


def _run_upload(video_id: str, source: Path, media_dir: Path, media_kind: str,
                thumbnail: Path | None, settings: dict, cancel: threading.Event) -> None:
    acquired = False
    staged = media_dir / "derived" / ".mp3.partial.mp3"
    try:
        while not cancel.is_set():
            acquired = _SLOTS.acquire(timeout=0.2)
            if acquired:
                break
        if cancel.is_set():
            return
        db.update_video(video_id, status="processing", progress=70, stage="checking")
        playback_warning = None
        if media_kind == "audio":
            db.upsert_media_conversion(video_id, "mp3", "converting")
            db.update_video(video_id, stage="converting")
            target = staged.parent / "mp3.mp3"
            if source.suffix == ".mp3":
                # Preserve uploaded MP3 bytes; normalization is only needed for other codecs.
                target = source
            else:
                if not media.convert_to_mp3(source, staged, cancel):
                    raise ValueError("The uploaded audio could not be prepared for MP3 playback.")
                if cancel.is_set():
                    raise InterruptedError("Upload cancelled")
                staged.replace(target)
            db.update_media_conversion(video_id, "mp3", status="completed", output_path=str(target),
                                       completed_at=datetime.now(timezone.utc).isoformat())
        elif not media.is_browser_compatible(source):
            playback_warning = ("This uploaded format may not play in your browser. "
                                "Convert it to MP4 for playback or download the original file.")
        if thumbnail is None and settings.get("generate_thumbnails", True):
            candidate = media_dir / "thumbnail.jpg"
            if media.make_thumbnail(source, candidate):
                thumbnail = candidate
        with _MEDIA_EDIT_LOCK:
            if cancel.is_set() or not db.get_video(video_id):
                raise InterruptedError("Upload cancelled")
            db.complete_video(video_id, completed_at=datetime.now(timezone.utc).isoformat(),
                              progress=100, stage=None, playback_warning=playback_warning,
                              thumbnail_path=str(thumbnail) if thumbnail else None,
                              playback_format="mp3" if media_kind == "audio" else "original")
        video_keywords.schedule(video_id)
    except InterruptedError:
        pass
    except Exception as exc:
        if not cancel.is_set():
            db.update_video(video_id, status="failed", stage=None, error_message=str(exc)[:2000],
                            completed_at=datetime.now(timezone.utc).isoformat())
            if media_kind == "audio":
                db.update_media_conversion(video_id, "mp3", status="failed", error_message=str(exc)[:2000])
    finally:
        staged.unlink(missing_ok=True)
        if acquired:
            _SLOTS.release()
        if cancel.is_set():
            shutil.rmtree(media_dir, ignore_errors=True)
        with _LOCK:
            _ACTIVE.pop(video_id, None)


def update_thumbnail(video_id: str, thumbnail) -> dict:
    row = db.get_video(video_id)
    if not row:
        raise LookupError("Video not found")
    if row["status"] != "ready":
        raise RuntimeError("Wait for media preparation to finish before changing its thumbnail")
    with tempfile.TemporaryDirectory(prefix="thumbnail-", dir=temp_manager.library_root()) as folder:
        staged = Path(folder) / "image.upload"
        normalized = Path(folder) / "image.jpg"
        _copy_upload(thumbnail, staged, config.MAX_THUMBNAIL_BYTES)
        media.normalize_thumbnail(staged, normalized)
        with _MEDIA_EDIT_LOCK:
            row = db.get_video(video_id)
            if not row:
                raise LookupError("Video not found")
            media_dir = Path(row["media_dir"])
            old = Path(row["thumbnail_path"]) if row.get("thumbnail_path") else None
            target = media_dir / f"custom-{uuid.uuid4().hex}.jpg"
            normalized.replace(target)
            try:
                db.update_video(video_id, thumbnail_path=str(target))
            except Exception:
                target.unlink(missing_ok=True)
                raise
            if old and old.resolve().is_relative_to(media_dir.resolve()):
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    # A Windows response may still hold the previous image open.
                    # It remains inside this media directory for normal deletion.
                    _LOG.warning("Previous artwork for %s is still in use.", video_id)
            return db.get_video(video_id)


def cancel_and_delete(video_id: str) -> None:
    with _LOCK:
        _DELETING.add(video_id)
        entry = _ACTIVE.get(video_id)
        conversions = [value for (item_id, _), value in _CONVERSIONS.items()
                       if item_id == video_id]
    if entry:
        entry["cancel"].set()
        entry["thread"].join(timeout=2)
    for conversion in conversions:
        conversion["cancel"].set()
    for conversion in conversions:
        conversion["thread"].join(timeout=2)
    try:
        with _MEDIA_EDIT_LOCK:
            row = db.get_video(video_id)
            if row and row.get("media_dir"):
                media_dir = Path(row["media_dir"])
                for _ in range(10):
                    shutil.rmtree(media_dir, ignore_errors=True)
                    if not media_dir.exists():
                        break
                    threading.Event().wait(0.2)
            db.delete_video(video_id)
    finally:
        with _LOCK:
            _DELETING.discard(video_id)


def purge_expired() -> list[str]:
    """Delete ready downloads whose per-video retention deadline has passed."""
    deleted = []
    with db.RETENTION_POLICY_LOCK:
        for row in db.list_expired_videos():
            cancel_and_delete(row["id"])
            deleted.append(row["id"])
    return deleted


def update_video_retention(video_id: str, retention_days: int) -> dict:
    row = db.update_video_retention(video_id, retention_days)
    if row is None:
        raise LookupError("Video not found")
    return row


def video_payload(row: dict) -> dict:
    payload = {k: v for k, v in row.items()
               if k not in ("media_dir", "file_path", "thumbnail_path")}
    video_id = row["id"]
    payload["stream_url"] = f"/api/media/{video_id}/stream"
    payload["thumbnail_url"] = (f"/api/media/{video_id}/thumbnail"
                                if row.get("thumbnail_path") else None)
    if row.get("thumbnail_path") and Path(row["thumbnail_path"]).name.startswith("custom-"):
        payload["thumbnail_url"] += f"?v={Path(row['thumbnail_path']).stem}"
    payload.setdefault("media_kind", "video")
    payload.setdefault("playback_format", "original")
    payload["playback_preference_explicit"] = bool(
        row.get("playback_preference_explicit", False))
    payload["download_url"] = f"/api/media/{video_id}/download"
    payload["keywords"] = db.get_video_keywords(video_id)
    file_path = row.get("file_path")
    payload["file_name"] = Path(file_path).name if file_path else None
    payload["conversions"] = {
        output_format: conversion_payload(row["id"], output_format)
        for output_format in sorted(_CONVERSION_FORMATS)
    }
    return payload


def conversion_payload(video_id: str, output_format: str) -> dict:
    if output_format not in _CONVERSION_FORMATS:
        raise ValueError("Unsupported conversion format")
    row = db.get_media_conversion(video_id, output_format)
    status = row["status"] if row else "idle"
    output = Path(row["output_path"]) if row and row.get("output_path") else None
    if status == "completed" and (not output or not output.is_file()):
        status = "failed"
    payload = {
        "format": output_format,
        "status": status,
        "error_message": (row.get("error_message") if row else None),
        "download_url": None,
        "stream_url": None,
    }
    if status == "completed":
        base = f"/api/media/{video_id}/conversions/{output_format}"
        payload["download_url"] = f"{base}/download"
        payload["stream_url"] = f"{base}/stream"
    return payload


def update_playback(video_id: str, output_format: str) -> dict:
    if output_format not in ("original", "mp3"):
        raise ValueError("Playback format must be original or mp3")
    with _MEDIA_EDIT_LOCK:
        row = db.get_video(video_id)
        if not row:
            raise LookupError("Video not found")
        if row["status"] != "ready":
            raise RuntimeError("Media is not ready for playback")
        if output_format == "mp3" and conversion_payload(video_id, "mp3")["status"] != "completed":
            raise RuntimeError("Convert this media to MP3 before selecting MP3 playback")
        db.update_video(video_id, playback_format=output_format,
                        playback_preference_explicit=True)
        return db.get_video(video_id)


def start_conversion(video_id: str, output_format: str) -> dict:
    if output_format not in _CONVERSION_FORMATS:
        raise ValueError("Unsupported conversion format")
    row = db.get_video(video_id)
    source = Path(row["file_path"]) if row and row.get("file_path") else None
    if not row:
        raise LookupError("Video not found")
    if row["status"] != "ready" or not source or not source.is_file():
        raise RuntimeError("Video is not ready for conversion")
    if row.get("media_kind") == "audio" and output_format == "mp4":
        thumbnail = Path(row["thumbnail_path"]) if row.get("thumbnail_path") else None
        if not thumbnail or not thumbnail.is_file():
            raise ValueError("Add a custom thumbnail before creating an MP4 from audio")

    existing = db.get_media_conversion(video_id, output_format)
    if existing and existing["status"] == "completed":
        output = Path(existing["output_path"]) if existing.get("output_path") else None
        if output and output.is_file():
            if output_format == "mp3":
                update_playback(video_id, "mp3")
            return conversion_payload(video_id, output_format)

    key = (video_id, output_format)
    with _LOCK:
        if video_id in _DELETING or not db.get_video(video_id):
            raise LookupError("Video not found")
        if key in _CONVERSIONS:
            return conversion_payload(video_id, output_format)
        db.upsert_media_conversion(video_id, output_format, "queued")
        cancel = threading.Event()
        thread = threading.Thread(
            target=_run_conversion,
            args=(video_id, output_format, source, Path(row["media_dir"]), cancel),
            daemon=True,
        )
        _CONVERSIONS[key] = {"cancel": cancel, "thread": thread}
        thread.start()
    return conversion_payload(video_id, output_format)


def wait_for_conversion(video_id: str, output_format: str,
                        timeout: float) -> dict | None:
    deadline = time.time() + timeout
    row = db.get_media_conversion(video_id, output_format)
    while time.time() < deadline:
        if row is None or row["status"] in ("completed", "failed"):
            return row
        time.sleep(0.1)
        row = db.get_media_conversion(video_id, output_format)
    return row


def _run_conversion(video_id: str, output_format: str, source: Path,
                    media_dir: Path, cancel: threading.Event) -> None:
    key = (video_id, output_format)
    derived_dir = media_dir / "derived"
    target = derived_dir / f"{output_format}.{output_format}"
    staged = derived_dir / f".{output_format}.partial.{output_format}"
    acquired = False
    try:
        while not cancel.is_set():
            acquired = _SLOTS.acquire(timeout=0.2)
            if acquired:
                break
        if cancel.is_set():
            return
        db.update_media_conversion(video_id, output_format, status="converting")
        staged.unlink(missing_ok=True)
        row = db.get_video(video_id)
        if output_format == "mp4" and row and row.get("media_kind") == "audio":
            thumbnail = Path(row["thumbnail_path"]) if row.get("thumbnail_path") else None
            converted = bool(thumbnail and thumbnail.is_file()
                             and media.convert_audio_to_mp4(source, thumbnail, staged, cancel))
        else:
            converter = (media.convert_to_mp4 if output_format == "mp4"
                         else media.convert_to_mp3)
            converted = converter(source, staged, cancel)
        if not converted:
            raise RuntimeError(f"The media could not be converted to {output_format.upper()}.")
        with _MEDIA_EDIT_LOCK:
            if cancel.is_set() or not db.get_video(video_id):
                raise InterruptedError("Conversion cancelled")
            staged.replace(target)
            db.complete_media_conversion(video_id, output_format, str(target))
    except InterruptedError:
        staged.unlink(missing_ok=True)
    except Exception as exc:
        staged.unlink(missing_ok=True)
        if not cancel.is_set():
            db.update_media_conversion(
                video_id, output_format, status="failed",
                error_message=str(exc)[:2000],
                completed_at=datetime.now(timezone.utc).isoformat())
    finally:
        if acquired:
            _SLOTS.release()
        if cancel.is_set() and not db.get_video(video_id):
            shutil.rmtree(media_dir, ignore_errors=True)
        with _LOCK:
            _CONVERSIONS.pop(key, None)


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

        if getattr(connector, "accepts_download_settings", False):
            result = connector.download(url, media_dir, quality, on_progress, cancel,
                                        download_settings=settings)
        else:
            # Preserve compatibility with custom connectors implementing the
            # original five-argument contract.
            result = connector.download(url, media_dir, quality, on_progress, cancel)
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
        completed = datetime.now(timezone.utc)
        db.complete_video(
            video_id, completed_at=completed.isoformat(), progress=100, stage=None,
            file_path=str(path), file_size=path.stat().st_size,
            thumbnail_path=str(thumbnail) if thumbnail else None,
            playback_warning=playback_warning,
            title=result.info.title, uploader=result.info.uploader,
            description=result.info.description,
            duration=result.info.duration, width=result.info.width,
            height=result.info.height)
        video_keywords.schedule(video_id)
        _remove_downloaded_from_watch_later(url)
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
