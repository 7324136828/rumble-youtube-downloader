"""Bounded metadata cache with shared searches and independent caller guards."""
import copy
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass

MAX_ENTRIES = 128
SUCCESS_TTL = 90
EMPTY_TTL = 20
UNAVAILABLE_TTL = 10
MAX_STALE_AGE = 15 * 60
WAIT_INTERVAL = 0.15
STALE_WARNING = "Using recently found videos because live search is temporarily unavailable."


@dataclass(frozen=True)
class _Entry:
    response: dict
    expires_at: float
    success: dict | None = None
    success_at: float | None = None


_LOCK = threading.Lock()
_CACHE: OrderedDict[tuple, _Entry] = OrderedDict()
_INFLIGHT: dict[tuple, Future] = {}
_GENERATION = 0
_ABANDONED = object()


def clear_cache():
    """Clear metadata and detach pending work without stranding its waiters."""
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
        _CACHE.clear()
        _INFLIGHT.clear()


def _entry(response: dict, previous: _Entry | None, now: float) -> _Entry:
    response = copy.deepcopy(response)
    if response.get("status") == "unavailable":
        return _Entry(response, now + UNAVAILABLE_TTL,
                      previous.success if previous else None,
                      previous.success_at if previous else None)
    if response.get("results"):
        return _Entry(response, now + SUCCESS_TTL, response, now)
    # A successful empty search supersedes older hits; they must not reappear
    # merely because a later request fails.
    return _Entry(response, now + EMPTY_TTL)


def _response(entry: _Entry, now: float) -> dict:
    if (entry.response.get("status") == "unavailable" and not entry.response.get("results")
            and entry.success is not None and entry.success_at is not None
            and 0 <= now - entry.success_at <= MAX_STALE_AGE):
        result = copy.deepcopy(entry.success)
        result["warnings"] = list(dict.fromkeys([
            *result.get("warnings", []), *entry.response.get("warnings", []), STALE_WARNING,
        ]))
        result["cache_status"] = "stale"
        result["cache_age_seconds"] = int(now - entry.success_at)
        return result
    return copy.deepcopy(entry.response)


def cached_search(key: tuple, fetch, guard=lambda: None) -> dict:
    """Return discovery metadata, calling ``fetch(guard)`` at most once at a time.

    Callers supply provider/mode/query keys and retain responsibility for source
    and opt-in filtering. Only normal response dictionaries enter the cache.
    A cancelled owner relinquishes the search, allowing a valid waiter to take
    over; its request-specific guard exception never cancels those waiters.
    """
    while True:
        guard()
        with _LOCK:
            now = time.monotonic()
            existing = _CACHE.get(key)
            cached = existing if existing is not None and existing.expires_at > now else None
            if cached is not None:
                _CACHE.move_to_end(key)
                future, owner = None, False
            else:
                future = _INFLIGHT.get(key)
                owner = future is None
                if owner:
                    future = Future()
                    _INFLIGHT[key] = future
                generation = _GENERATION
        if cached is not None:
            result = _response(cached, time.monotonic())
            guard()
            return result
        if not owner:
            while True:
                guard()
                try:
                    completed = future.result(timeout=WAIT_INTERVAL)
                    break
                except FutureTimeout:
                    if future.done():
                        # A fetch can itself raise TimeoutError. It is not a
                        # polling timeout. Re-read to distinguish that error
                        # from a success racing with the polling deadline.
                        guard()
                        completed = future.result()
                        break
                    continue
                except BaseException:
                    guard()
                    raise
            guard()
            if completed is _ABANDONED:
                continue
            result = _response(completed, time.monotonic())
            guard()
            return result

        guard_failed = False

        def owner_guard():
            nonlocal guard_failed
            try:
                guard()
            except BaseException:
                guard_failed = True
                raise

        try:
            owner_guard()
            response = fetch(owner_guard)
            owner_guard()
            if not isinstance(response, dict):
                raise TypeError("Search fetch must return a response dictionary.")
            with _LOCK:
                same_generation = generation == _GENERATION
                completed = _entry(response, _CACHE.get(key) if same_generation else None,
                                   time.monotonic())
                if same_generation and _INFLIGHT.get(key) is future:
                    _CACHE[key] = completed
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > MAX_ENTRIES:
                        _CACHE.popitem(last=False)
                if _INFLIGHT.get(key) is future:
                    del _INFLIGHT[key]
            future.set_result(completed)
        except BaseException as exc:
            # Remove before waking waiters so they can immediately elect a new
            # owner. Identity checking protects a replacement after clear_cache.
            with _LOCK:
                if _INFLIGHT.get(key) is future:
                    del _INFLIGHT[key]
            if guard_failed or not isinstance(exc, Exception):
                future.set_result(_ABANDONED)
            else:
                # Programming/fetch errors are shared once, never cached or
                # retried as though they were another owner's cancellation.
                future.set_exception(exc)
            raise
        result = _response(completed, time.monotonic())
        guard()
        return result
