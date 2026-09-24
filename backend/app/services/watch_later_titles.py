"""Bounded background title retrieval for explicitly saved video links."""
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .. import config
from . import ai_title_lookup, db, recommendation_providers, video_title_lookup, watch_later_thumbnails

MAX_PENDING = 256
LOOKUP_TIMEOUT = 75
_POOL = None
_LOCK = threading.Lock()
_PENDING = {}
_LOG = logging.getLogger(__name__)


class _Cancelled(Exception):
    pass


def _database_key():
    return str(config.JOBS_DB_PATH.resolve())


def shutdown():
    """Discard queued jobs; running workers stop at their next guard check."""
    global _POOL
    with _LOCK:
        pool, _POOL = _POOL, None
        _PENDING.clear()
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)
    watch_later_thumbnails.shutdown()


def _provider(item):
    providers = recommendation_providers.configured_providers(
        db.get_recommendation_settings().get("providers"), enabled_only=False)
    return next((provider for provider in providers if provider["id"] == item["connector"]), None)


def _run(key, token, item, provider, force=False):
    deadline = time.monotonic() + LOOKUP_TIMEOUT

    def guard():
        with _LOCK:
            if _PENDING.get(token) != key:
                raise _Cancelled()
        if _database_key() != key[0]:
            raise _Cancelled()
        current = db.get_watch_later_item(key[1], title_fetch_token=token)
        if current is None or _provider(current) is None:
            raise _Cancelled()
        if time.monotonic() >= deadline:
            raise video_title_lookup.TitleLookupError("Title lookup timed out. Try again later.")

    title, error, method = None, None, None
    try:
        guard()
        title = video_title_lookup.lookup_title(item["source_url"], provider, guard)
        method = "source"
        guard()
    except _Cancelled:
        error = "Title lookup stopped because the saved video or website changed."
    except video_title_lookup.TitleLookupError as exc:
        try:
            title = ai_title_lookup.lookup_title(item["source_url"], provider,
                                                 db.get_recommendation_settings(), guard)
            method = "ai"
        except _Cancelled:
            error = "Title lookup stopped because the saved video or website changed."
        except ai_title_lookup.AiTitleError as ai_exc:
            error = str(ai_exc) if db.get_recommendation_settings().get("allow_ai_title_lookup") else str(exc)
    except Exception as exc:
        # Avoid reflecting upstream bodies, URLs, or database paths to the UI.
        _LOG.warning("Watch later title lookup failed (%s).", type(exc).__name__)
        error = "Could not fetch a title. Try again later."
    finally:
        try:
            if _database_key() == key[0]:
                db.finish_watch_later_title(key[1], token, title=title if error is None else None,
                                            error=error, method=method, force=force,
                                            expected_title=item.get("title"))
        finally:
            with _LOCK:
                _PENDING.pop(token, None)


def request_title(catalog_id, force=False):
    """Return immediately after scheduling; repeated requests share one job."""
    global _POOL
    item = db.get_watch_later_item(catalog_id)
    if item is None:
        raise LookupError("Saved video not found.")
    if not force and item.get("title") and item["title"] != item["source_url"]:
        return {"item": item, "queued": False}
    provider = _provider(item)
    if provider is None:
        raise ValueError("Add this video's website to Recommendation websites before fetching its title.")
    key = (_database_key(), catalog_id)
    queued = False
    with _LOCK:
        token = uuid.uuid4().hex
        claimed = db.begin_watch_later_title(catalog_id, token, force=force)
        if claimed is not None:
            if len(_PENDING) >= MAX_PENDING:
                db.finish_watch_later_title(catalog_id, token,
                    error="Title lookup is busy. Try Fetch title again shortly.", force=force,
                    expected_title=claimed.get("title"))
            else:
                _PENDING[token] = key
                try:
                    if _POOL is None:
                        _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="watch-later-title")
                    _POOL.submit(_run, key, token, claimed, provider, force)
                except RuntimeError:
                    _PENDING.pop(token, None)
                    db.finish_watch_later_title(catalog_id, token,
                        error="Title lookup is unavailable. Try again after restarting the app.", force=force,
                        expected_title=claimed.get("title"))
                else:
                    queued = True
    current = db.get_watch_later_item(catalog_id)
    if current is None:
        raise LookupError("Saved video not found.")
    return {"item": current, "queued": queued or current.get("title_fetch_status") == "pending"}


def schedule_items(items):
    """Schedule missing titles after an import has committed successfully."""
    result = []
    for item in items:
        try:
            result.append(request_title(item["catalog_id"])["item"])
        except (LookupError, ValueError):
            # A concurrent removal/provider edit must not undo a saved import.
            result.append(item)
    return watch_later_thumbnails.schedule_items(result)
