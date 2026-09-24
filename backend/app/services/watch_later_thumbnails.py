"""Bounded background thumbnail downloads for explicitly saved videos."""
import logging
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config
from . import db

MAX_PENDING = 256
MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024
THUMBNAIL_TIMEOUT = 35
_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
_POOL = None
_LOCK = threading.Lock()
_PENDING = {}
_LOG = logging.getLogger(__name__)


def _database_key():
    return str(config.JOBS_DB_PATH.resolve())


def _root():
    root = (config.JOBS_ROOT / "watch_later_thumbnails").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _candidates(catalog_id):
    root = _root()
    return [path for path in root.glob(f"{catalog_id}.*")
            if path.resolve().parent == root and path.suffix.lower() in _EXTENSIONS]


def shutdown():
    global _POOL
    with _LOCK:
        pool, _POOL = _POOL, None
        _PENDING.clear()
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def remove_local(catalog_id):
    value = db.get_watch_later_thumbnail(catalog_id)
    if not value:
        return
    path = Path(value).resolve()
    root = _root()
    if path.is_relative_to(root):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _LOG.info("Could not remove Watch later thumbnail for catalog item %s.", catalog_id)


def _run(key, token, item):
    path = None
    error = "A thumbnail could not be downloaded from this video website."
    try:
        for candidate in _candidates(item["catalog_id"]):
            candidate.unlink(missing_ok=True)
        output = str(_root() / f"{item['catalog_id']}.%(ext)s")
        command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
                   "--skip-download", "--write-thumbnail", "--no-playlist", "--no-cache-dir",
                   "--socket-timeout", "8", "--retries", "0", "--extractor-retries", "0",
                   "--quiet", "--no-warnings", "--output", output, "--", item["source_url"]]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=THUMBNAIL_TIMEOUT, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        candidates = _candidates(item["catalog_id"])
        valid = [candidate for candidate in candidates
                 if candidate.is_file() and 0 < candidate.stat().st_size <= MAX_THUMBNAIL_BYTES]
        if not process.returncode and len(valid) == 1:
            path = str(valid[0].resolve())
        else:
            for candidate in candidates:
                candidate.unlink(missing_ok=True)
    except (OSError, subprocess.TimeoutExpired):
        _LOG.info("Watch later thumbnail lookup failed for catalog item %s.", item["catalog_id"])
    finally:
        try:
            saved = _database_key() == key[0] and db.finish_watch_later_thumbnail(
                key[1], token, path=path, error=error)
            if path and not saved:
                Path(path).unlink(missing_ok=True)
        finally:
            with _LOCK:
                _PENDING.pop(token, None)


def request_thumbnail(catalog_id):
    global _POOL
    item = db.get_watch_later_item(catalog_id)
    if item is None:
        raise LookupError("Saved video not found.")
    if item.get("thumbnail_fetch_status") == "ready":
        return {"item": item, "queued": False}
    key = (_database_key(), catalog_id)
    queued = False
    with _LOCK:
        token = uuid.uuid4().hex
        claimed = db.begin_watch_later_thumbnail(catalog_id, token)
        if claimed is not None:
            if len(_PENDING) >= MAX_PENDING:
                db.finish_watch_later_thumbnail(catalog_id, token,
                    error="Thumbnail lookup is busy. Save or retry this video again shortly.")
            else:
                _PENDING[token] = key
                try:
                    if _POOL is None:
                        _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="watch-later-thumbnail")
                    _POOL.submit(_run, key, token, claimed)
                except RuntimeError:
                    _PENDING.pop(token, None)
                    db.finish_watch_later_thumbnail(catalog_id, token,
                        error="Thumbnail lookup is unavailable. Try again after restarting the app.")
                else:
                    queued = True
    current = db.get_watch_later_item(catalog_id)
    if current is None:
        raise LookupError("Saved video not found.")
    return {"item": current, "queued": queued or current.get("thumbnail_fetch_status") == "pending"}


def schedule_items(items):
    result = []
    for item in items:
        try:
            result.append(request_thumbnail(item["catalog_id"])["item"])
        except LookupError:
            result.append(item)
    return result
