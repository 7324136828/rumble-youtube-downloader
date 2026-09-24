"""Source diagnostics retain first-party cache provenance and retry history."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import recommendation_providers as providers, recommendation_tools as tools

PROVIDER = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}


def video(ident="1", verified=True):
    return {"id": "vimeo.com:" + ident, "source_url": "https://vimeo.com/" + ident,
            "connector": "vimeo.com", "title": "Nature", "verified": verified,
            "verification": "provider_search" if verified else "custom_search",
            "origins": ["custom_search"]}


def channel(count=0, status="empty", *, age=None, warnings=None, stale=False):
    value = {"origin": "custom_search", "count": count, "status": status,
             "discovery": "provider_search", "warnings": warnings or []}
    if stale:
        value["cache_status"] = "stale"
    if age is not None:
        value["cache_age_seconds"] = age
    return value


def response(items, channels, warnings=None, status=None):
    return {"results": items, "sources": channels, "warnings": warnings or [],
            "discovery": "provider_search", "status": status or ("ok" if items else "empty")}


class RecommendationSourceDiagnosticsTest(unittest.TestCase):
    def call(self, *responses):
        with patch.object(providers, "search_website", side_effect=responses) as fetch, \
                patch.object(tools.search, "search_videos",
                             side_effect=AssertionError("Unexpected built-in request")):
            result = tools.search_by_key_words(["birds", "nature"], providers=[PROVIDER])
        self.assertEqual(fetch.call_count, len(responses))
        return result

    def test_stale_result_sets_provider_cached_status_and_retains_age(self):
        result = self.call(response([video()], [channel(1, "ok", stale=True, age=150)]))
        report = result["sources"][0]
        self.assertEqual(report["status"], "cached")
        self.assertEqual(report["cache_age_seconds"], 150)
        self.assertEqual(report["channels"][0]["status"], "cached")
        self.assertTrue(result["results"][0]["verified"])

    def test_failure_from_earlier_topic_survives_second_attempt(self):
        first = response([], [channel(status="unavailable", warnings=["Website search failed."])],
                         ["Website search failed."], "unavailable")
        second = response([], [channel()])
        result = self.call(first, second)
        report = result["sources"][0]
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(report["channels"][0]["attempts"], 2)
        self.assertEqual(report["channels"][0]["warnings"], ["Website search failed."])

    def test_later_recovery_retains_history_without_repeating_initial_global_failure(self):
        first = response([], [channel(status="unavailable", warnings=["Initial failure."])],
                         ["Initial failure."], "unavailable")
        second = response([video()], [channel(1, "ok", stale=True, age=180,
                                                warnings=["Using cached metadata."])],
                          ["Using cached metadata."])
        result = self.call(first, second)
        report = result["sources"][0]
        self.assertEqual(report["status"], "cached")
        self.assertEqual(report["cache_age_seconds"], 180)
        self.assertEqual(report["channels"][0]["warnings"],
                         ["Initial failure.", "Using cached metadata."])
        self.assertEqual(result["warnings"], ["Using cached metadata."])

    def test_merging_keyword_reports_keeps_checks_and_live_status(self):
        first = {**channel(2, "ok", stale=True, age=350),
                 "checks": [{"method": "configured", "status": "cached"}]}
        second = {**channel(1, "ok"), "checks": [{"method": "configured", "status": "ok"}]}
        merged = tools._merge_channels([first, second])
        self.assertEqual([item["origin"] for item in merged], ["custom_search"])
        self.assertEqual(merged[0]["status"], "ok")
        self.assertEqual(merged[0]["count"], 3)
        self.assertEqual(merged[0]["cache_age_seconds"], 350)
        self.assertNotIn("cache_status", merged[0])
        self.assertEqual([check["status"] for check in merged[0]["checks"]], ["cached", "ok"])


if __name__ == "__main__":
    unittest.main()
