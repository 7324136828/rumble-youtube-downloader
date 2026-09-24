"""Offline TTL, stale metadata, concurrency, and cancellation regressions."""
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import search_cache as cache


def found(verified=True):
    return {"results": [{"id": "vimeo.com:123", "source_url": "https://vimeo.com/123",
                         "verified": verified,
                         "verification": "provider_search" if verified else "web_search"}],
            "warnings": [], "status": "ok"}


def empty():
    return {"results": [], "warnings": [], "status": "empty"}


def unavailable():
    return {"results": [], "warnings": ["Live search unavailable."], "status": "unavailable"}


class SearchCacheTest(unittest.TestCase):
    def setUp(self):
        cache.clear_cache()
        self.addCleanup(cache.clear_cache)
        self.key = ("native", "vimeo.com", "birds")

    def test_success_empty_and_unavailable_have_distinct_ttls(self):
        for result, ttl in ((found(), 90), (empty(), 20), (unavailable(), 10)):
            with self.subTest(status=result["status"]):
                cache.clear_cache()
                fetch = Mock(return_value=result)
                with patch.object(cache.time, "monotonic", return_value=0) as clock:
                    self.assertEqual(cache.cached_search(self.key, fetch), result)
                    clock.return_value = ttl - 0.01
                    self.assertEqual(cache.cached_search(self.key, fetch), result)
                    self.assertEqual(fetch.call_count, 1)
                    clock.return_value = ttl
                    cache.cached_search(self.key, fetch)
                    self.assertEqual(fetch.call_count, 2)

    def test_results_are_deep_copied_and_keep_verification(self):
        original = found(False)
        fetch = Mock(return_value=original)
        first = cache.cached_search(self.key, fetch)
        original["results"][0]["verified"] = True
        first["results"][0]["source_url"] = "changed"
        first["warnings"].append("changed")
        second = cache.cached_search(self.key, fetch)
        self.assertFalse(second["results"][0]["verified"])
        self.assertEqual(second["results"][0]["verification"], "web_search")
        self.assertEqual(second["results"][0]["source_url"], "https://vimeo.com/123")
        self.assertEqual(second["warnings"], [])
        fetch.assert_called_once()

    def test_lru_is_bounded_and_recent_reads_prevent_eviction(self):
        fetch = Mock(return_value=empty())
        with patch.object(cache.time, "monotonic", return_value=0):
            for index in range(cache.MAX_ENTRIES):
                cache.cached_search((index,), fetch)
            cache.cached_search((0,), fetch)
            cache.cached_search((cache.MAX_ENTRIES,), fetch)
            self.assertEqual(len(cache._CACHE), cache.MAX_ENTRIES)
            cache.cached_search((0,), fetch)
            self.assertEqual(fetch.call_count, cache.MAX_ENTRIES + 1)
            cache.cached_search((1,), fetch)
            self.assertEqual(fetch.call_count, cache.MAX_ENTRIES + 2)

    def test_guard_runs_on_cache_hit_and_after_fetch(self):
        cache.cached_search(self.key, lambda guard: found())
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            cache.cached_search(self.key, Mock(), lambda: (_ for _ in ()).throw(RuntimeError("disabled")))
        cache.clear_cache()
        active = True

        def guard():
            if not active:
                raise RuntimeError("disabled")

        def fetch(check):
            nonlocal active
            active = False
            return found()

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            cache.cached_search(self.key, fetch, guard)
        self.assertEqual(len(cache._CACHE), 0)
        self.assertEqual(len(cache._INFLIGHT), 0)

    def test_concurrent_searches_share_one_fetch(self):
        entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
        calls = 0
        checks = 0

        def fetch(guard):
            nonlocal calls
            calls += 1
            entered.set()
            self.assertTrue(release.wait(3))
            guard()
            return found()

        def waiter_guard():
            nonlocal checks
            checks += 1
            if checks >= 2:
                waiting.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            owner = executor.submit(cache.cached_search, self.key, fetch)
            self.assertTrue(entered.wait(3))
            waiter = executor.submit(cache.cached_search, self.key, fetch, waiter_guard)
            self.assertTrue(waiting.wait(3))
            release.set()
            first, second = owner.result(3), waiter.result(3)
        self.assertEqual(calls, 1)
        self.assertEqual(first, second)
        first["results"][0]["verified"] = False
        self.assertTrue(second["results"][0]["verified"])

    def test_cancelled_owner_hands_work_to_valid_waiter(self):
        entered, release, waiting, cancelled = [threading.Event() for _ in range(4)]
        checks = 0

        def owner_guard():
            if cancelled.is_set():
                raise RuntimeError("owner request expired")

        def owner_fetch(guard):
            entered.set()
            self.assertTrue(release.wait(3))
            guard()
            return found()

        def waiter_guard():
            nonlocal checks
            checks += 1
            if checks >= 2:
                waiting.set()

        replacement = Mock(return_value=found(False))
        with ThreadPoolExecutor(max_workers=2) as executor:
            owner = executor.submit(cache.cached_search, self.key, owner_fetch, owner_guard)
            self.assertTrue(entered.wait(3))
            waiter = executor.submit(cache.cached_search, self.key, replacement, waiter_guard)
            self.assertTrue(waiting.wait(3))
            cancelled.set()
            release.set()
            with self.assertRaisesRegex(RuntimeError, "owner request expired"):
                owner.result(3)
            result = waiter.result(3)
        replacement.assert_called_once()
        self.assertFalse(result["results"][0]["verified"])
        self.assertEqual(len(cache._INFLIGHT), 0)

    def test_cancelled_waiter_does_not_cancel_owner(self):
        entered, release, waiting, cancelled = [threading.Event() for _ in range(4)]
        checks = 0

        def fetch(guard):
            entered.set()
            self.assertTrue(release.wait(3))
            guard()
            return found()

        def waiter_guard():
            nonlocal checks
            checks += 1
            if cancelled.is_set():
                raise RuntimeError("waiter request expired")
            if checks >= 2:
                waiting.set()

        replacement = Mock(return_value=empty())
        with ThreadPoolExecutor(max_workers=2) as executor:
            owner = executor.submit(cache.cached_search, self.key, fetch)
            self.assertTrue(entered.wait(3))
            waiter = executor.submit(cache.cached_search, self.key, replacement, waiter_guard)
            self.assertTrue(waiting.wait(3))
            cancelled.set()
            with self.assertRaisesRegex(RuntimeError, "waiter request expired"):
                waiter.result(2)
            release.set()
            self.assertEqual(owner.result(3), found())
        self.assertEqual(cache.cached_search(self.key, replacement), found())
        replacement.assert_not_called()

    def test_fetch_exceptions_are_shared_once_without_retry_or_cache(self):
        for exception in (ValueError("bad provider data"), TimeoutError("fetch timed out")):
            with self.subTest(exception=type(exception).__name__):
                cache.clear_cache()
                entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
                checks = 0

                def broken(guard):
                    entered.set()
                    self.assertTrue(release.wait(3))
                    raise exception

                def waiter_guard():
                    nonlocal checks
                    checks += 1
                    if checks >= 2:
                        waiting.set()

                replacement = Mock(return_value=found())
                with ThreadPoolExecutor(max_workers=2) as executor:
                    owner = executor.submit(cache.cached_search, self.key, broken)
                    self.assertTrue(entered.wait(3))
                    waiter = executor.submit(cache.cached_search, self.key, replacement, waiter_guard)
                    self.assertTrue(waiting.wait(3))
                    release.set()
                    for future in (owner, waiter):
                        with self.assertRaises(type(exception)):
                            future.result(3)
                replacement.assert_not_called()
                self.assertEqual(cache.cached_search(self.key, replacement), found())
                replacement.assert_called_once()

    def test_non_dictionary_results_are_not_cached(self):
        with self.assertRaises(TypeError):
            cache.cached_search(self.key, lambda guard: None)
        self.assertEqual(cache.cached_search(self.key, lambda guard: found()), found())

    def test_stale_on_unavailable_preserves_provenance_and_original_age(self):
        for verified in (False, True):
            with self.subTest(verified=verified):
                cache.clear_cache()
                with patch.object(cache.time, "monotonic", return_value=0) as clock:
                    cache.cached_search(self.key, lambda guard: found(verified))
                    clock.return_value = 100
                    failing = Mock(return_value=unavailable())
                    result = cache.cached_search(self.key, failing)
                    self.assertEqual(result["cache_status"], "stale")
                    self.assertEqual(result["cache_age_seconds"], 100)
                    self.assertEqual(result["results"][0]["verified"], verified)
                    self.assertIn(cache.STALE_WARNING, result["warnings"])
                    self.assertIn("Live search unavailable.", result["warnings"])
                    clock.return_value = 105
                    result = cache.cached_search(self.key, failing)
                    self.assertEqual(result["cache_age_seconds"], 105)
                    self.assertEqual(failing.call_count, 1)
                    clock.return_value = 110
                    result = cache.cached_search(self.key, failing)
                    self.assertEqual(result["cache_age_seconds"], 110)
                    self.assertEqual(failing.call_count, 2)

    def test_stale_expiry_also_applies_during_negative_cache_ttl(self):
        with patch.object(cache.time, "monotonic", return_value=0) as clock:
            cache.cached_search(self.key, lambda guard: found())
            failing = Mock(return_value=unavailable())
            clock.return_value = cache.MAX_STALE_AGE - 1
            self.assertEqual(cache.cached_search(self.key, failing)["cache_status"], "stale")
            clock.return_value = cache.MAX_STALE_AGE + 1
            result = cache.cached_search(self.key, failing)
            self.assertEqual(result, unavailable())
            self.assertEqual(failing.call_count, 1)
            clock.return_value += cache.UNAVAILABLE_TTL
            self.assertEqual(cache.cached_search(self.key, failing), unavailable())

    def test_authoritative_empty_does_not_return_old_success(self):
        with patch.object(cache.time, "monotonic", return_value=0) as clock:
            cache.cached_search(self.key, lambda guard: found())
            clock.return_value = 90
            self.assertEqual(cache.cached_search(self.key, lambda guard: empty()), empty())
            clock.return_value = 110
            self.assertEqual(cache.cached_search(self.key, lambda guard: unavailable()), unavailable())

    def test_modes_and_provider_keys_are_independent(self):
        fetch = Mock(return_value=found())
        for key in (("native", "vimeo.com", "birds"), ("public", "vimeo.com", "birds"),
                    ("native", "bilibili.tv", "birds"), ("native", "vimeo.com", "Birds")):
            cache.cached_search(key, fetch)
        self.assertEqual(fetch.call_count, 4)

    def test_clear_during_fetch_cannot_repopulate_cache_with_old_work(self):
        entered, release = threading.Event(), threading.Event()

        def fetch(guard):
            entered.set()
            self.assertTrue(release.wait(3))
            return found(False)

        with ThreadPoolExecutor(max_workers=1) as executor:
            owner = executor.submit(cache.cached_search, self.key, fetch)
            self.assertTrue(entered.wait(3))
            cache.clear_cache()
            cache.cached_search(self.key, lambda guard: found(True))
            release.set()
            self.assertFalse(owner.result(3)["results"][0]["verified"])
        self.assertTrue(cache.cached_search(self.key, Mock())["results"][0]["verified"])


if __name__ == "__main__":
    unittest.main()
