"""Manual provider search uses saved websites without requiring AI recommendations."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.schemas.recommendation_providers import RecommendationProvider, default_providers
from app.schemas.search import SearchResponse, SearchResult
from app.services import connector_client, db, manual_video_search, recommendation_providers, search


def website(domain, name=None, enabled=True):
    return RecommendationProvider(domain=domain, name=name or domain, enabled=enabled).model_dump()


def native_result(source="youtube", number=1):
    ident = f"{number:011d}" if source == "youtube" else f"v{number}abc"
    url = f"https://www.youtube.com/watch?v={ident}" if source == "youtube" else f"https://rumble.com/{ident}-video.html"
    return SearchResult(id=f"{source}:{ident}", title="Nature video", connector=source, source_url=url)


def custom_result(number=1, verified=True):
    return {"id": f"vimeo.com:{number}", "title": f"Nature film {number}", "connector": "vimeo.com",
            "source_url": f"https://vimeo.com/{number}", "verified": verified,
            "verification": "provider_search" if verified else "web_search"}


class ManualVideoSearchTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        db.init_db()
        self.settings = {"enabled": False, "model_id": None, "allow_unverified_links": False,
                         "providers": default_providers() + [website("vimeo.com", "Vimeo films")]}
        patcher = patch.object(db, "get_recommendation_settings", side_effect=lambda: self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        recommendation_providers.clear_search_state()
        self.addCleanup(recommendation_providers.clear_search_state)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_selected_custom_native_website_works_with_ai_disabled_and_long_query(self):
        topic = "a" * 150
        with patch.object(recommendation_providers, "search_website", return_value={"results": [custom_result()], "warnings": [], "status": "ok"}) as custom, \
                patch.object(search, "search_videos") as builtins, patch.object(connector_client, "complete") as model:
            response = self.client.get("/api/search", params={"q": topic, "source": "vimeo.com", "limit": 3})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "vimeo.com")
        self.assertEqual(payload["query"], topic)
        self.assertEqual(payload["results"][0]["provider_name"], "Vimeo films")
        self.assertEqual(payload["results"][0]["connector"], "vimeo.com")
        self.assertTrue(payload["results"][0]["verified"])
        self.assertEqual(custom.call_args.args[1:3], (topic, 4))
        self.assertTrue(callable(custom.call_args.args[3]))
        builtins.assert_not_called()
        model.assert_not_called()

    def test_all_interleaves_only_enabled_websites_and_preserves_web_verification(self):
        self.settings["providers"][1]["enabled"] = False
        with patch.object(search, "search_youtube", return_value=[native_result(number=1), native_result(number=2)]), \
                patch.object(search, "search_rumble") as rumble, \
                patch.object(recommendation_providers, "search_website", return_value={"results": [custom_result(1, False), custom_result(2, False)], "warnings": [], "status": "ok"}):
            response = self.client.get("/api/search", params={"q": "nature", "source": "all", "limit": 3})
        self.assertEqual(response.status_code, 200)
        items = response.json()["results"]
        self.assertEqual([item["connector"] for item in items], ["youtube", "vimeo.com", "youtube"])
        self.assertEqual([item["verified"] for item in items], [True, False, True])
        self.assertEqual(items[1]["verification"], "custom_search")
        rumble.assert_not_called()

    def test_load_more_keeps_links_and_only_user_marked_repeats_are_hidden(self):
        self.settings["providers"] = [default_providers()[0]]
        items = [native_result(number=index) for index in range(1, 25)]
        def response(query, source, limit):
            return SearchResponse(query=query, source=source, results=items[:limit], warnings=[])
        with patch.object(search, "search_videos", side_effect=response):
            first = self.client.get("/api/search", params={
                "q": "nature", "source": "youtube", "limit": 12, "session": "page-session"}).json()
            expanded = self.client.get("/api/search", params={
                "q": "nature", "source": "youtube", "limit": 24, "session": "page-session"}).json()
            later = self.client.get("/api/search", params={
                "q": "nature", "source": "youtube", "limit": 24, "session": "later-session"}).json()
            self.assertEqual(len(first["results"]), 12)
            self.assertTrue(first["has_more"])
            self.assertEqual(len(expanded["results"]), 24)
            self.assertFalse(expanded["has_more"])
            self.assertEqual(len(later["results"]), 24)
            marked = later["results"][0]
            self.assertEqual(self.client.patch(f"/api/settings/links/{marked['link_id']}",
                                              json={"state": "silenced"}).status_code, 200)
            self.assertEqual(next(item for item in db.list_link_history()["items"]
                                  if item["id"] == marked["link_id"])["state"], "silenced")
            after_review = self.client.get("/api/search", params={
                "q": "nature", "source": "youtube", "limit": 24, "session": "reviewed-session"}).json()
        self.assertNotIn(marked["source_url"], {item["source_url"] for item in after_review["results"]})
        self.assertEqual(len(after_review["results"]), 23,
                         [item["source_url"] for item in after_review["results"]])

    def test_manual_web_results_ignore_ai_unverified_preference_without_promoting_missing_flags(self):
        candidate = custom_result(2)
        del candidate["verified"]
        candidate["verification"] = "provider_search"
        with patch.object(recommendation_providers, "search_website", return_value={"results": [custom_result(1, False), candidate], "warnings": []}):
            response = self.client.get("/api/search", params={"q": "nature", "source": "vimeo.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 2)
        self.assertTrue(all(item["verified"] is False for item in response.json()["results"]))

    def test_fetch_all_returns_up_to_two_hundred_configured_page_links(self):
        self.settings["providers"][-1]["search_url"] = "https://vimeo.com/search?q={query}"
        candidates = [custom_result(number) for number in range(1, 41)]
        with patch.object(recommendation_providers, "search_website", return_value={
                "results": candidates, "warnings": [], "status": "ok"}) as custom:
            response = self.client.get("/api/search", params={
                "q": "nature", "source": "vimeo.com", "fetch_all": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 40)
        self.assertFalse(response.json()["has_more"])
        self.assertEqual(custom.call_args.args[1:3], ("nature", 200))
        self.assertTrue(callable(custom.call_args.args[3]))
        self.assertTrue(custom.call_args.kwargs["fetch_all"])

    def test_fetch_all_keeps_enriched_title_and_safe_thumbnail(self):
        self.settings["providers"][-1]["search_url"] = "https://vimeo.com/search?q={query}"
        candidate = {**custom_result(1, False), "title": "Actual birds title",
                     "verification": "custom_search",
                     "thumbnail_url": "https://i.vimeocdn.com/video/1.jpg"}
        with patch.object(recommendation_providers, "search_website", return_value={
                "results": [candidate], "warnings": [], "status": "ok"}):
            response = self.client.get("/api/search", params={
                "q": "birds", "source": "vimeo.com", "fetch_all": "true"})
        item = response.json()["results"][0]
        self.assertEqual(item["title"], "Actual birds title")
        self.assertEqual(item["thumbnail_url"], "https://i.vimeocdn.com/video/1.jpg")

    def test_advanced_search_metadata_keeps_plain_description_without_promoting_verification(self):
        candidate = {**custom_result(1, False), "title": "Coastal birdwatching",
                     "description": "  Watch <b>coastal birds</b> &amp; learn\n  about their habitat.  "}
        with patch.object(recommendation_providers, "search_website", return_value={"results": [candidate], "warnings": []}):
            response = self.client.get("/api/search", params={"q": "birds", "source": "vimeo.com"})
        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertEqual(item["title"], "Coastal birdwatching")
        self.assertEqual(item["source_url"], candidate["source_url"])
        self.assertEqual(item["description"], "Watch coastal birds & learn about their habitat.")
        self.assertFalse(item["verified"])
        self.assertEqual(item["verification"], "custom_search")

    def test_descriptions_are_optional_and_bounded_plain_text(self):
        candidates = [custom_result(1, False), {**custom_result(2, False), "description": " \n "},
                      {**custom_result(3, False), "description": {"html": "unsupported"}},
                      {**custom_result(4, False), "description": "<p>" + "Birds " * 300 + "</p>"}]
        with patch.object(recommendation_providers, "search_website", return_value={"results": candidates, "warnings": []}):
            response = self.client.get("/api/search", params={"q": "birds", "source": "vimeo.com"})
        self.assertEqual(response.status_code, 200)
        items = response.json()["results"]
        self.assertEqual([item["description"] for item in items[:3]], [None, None, None])
        self.assertEqual(len(items[3]["description"]), 500)
        self.assertNotIn("<", items[3]["description"])
        self.assertTrue(all(item["verified"] is False for item in items))

    def test_custom_urls_are_scoped_and_metadata_cannot_override_provider_identity(self):
        raw = [{**custom_result(), "id": "malicious id", "connector": "instagram.com", "provider_name": "Wrong label"},
               {**custom_result(), "source_url": "https://instagram.com/reel/Other"},
               {**custom_result(), "source_url": "http://127.0.0.1/private"},
               {**custom_result(), "source_url": "https://vimeo.com/channels/profile"},
               {**custom_result(), "source_url": "https://www.vimeo.com/1?utm_source=duplicate"}]
        with patch.object(recommendation_providers, "search_website", return_value={"results": raw, "warnings": []}):
            response = self.client.get("/api/search", params={"q": "nature", "source": "vimeo.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)
        item = response.json()["results"][0]
        self.assertEqual(item["id"], "vimeo.com:1")
        self.assertEqual(item["connector"], "vimeo.com")
        self.assertEqual(item["provider_name"], "Vimeo films")

    def test_unknown_disabled_and_empty_provider_selection_perform_no_network(self):
        self.settings["providers"][0]["enabled"] = False
        with patch.object(search, "search_videos") as builtins, patch.object(recommendation_providers, "search_website") as custom:
            for source in ("youtube", "instagram.com", "other", "all.invalid"):
                self.assertEqual(self.client.get("/api/search", params={"q": "nature", "source": source}).status_code, 400)
            self.settings["providers"] = []
            response = self.client.get("/api/search", params={"q": "nature"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["results"], [])
        builtins.assert_not_called()
        custom.assert_not_called()

    def test_partial_custom_failure_keeps_other_sites_and_attributes_warning(self):
        with patch.object(search, "search_youtube", return_value=[native_result()]), \
                patch.object(search, "search_rumble", return_value=[]), \
                patch.object(recommendation_providers, "search_website", return_value={"results": [], "warnings": ["Vimeo films search is unavailable."], "status": "unavailable"}):
            response = self.client.get("/api/search", params={"q": "nature"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["connector"], "youtube")
        self.assertEqual(response.json()["warnings"][0]["source"], "vimeo.com")

    def test_malformed_or_empty_failure_warnings_still_return_readable_errors(self):
        self.settings["providers"] = [website("vimeo.com", "Vimeo films")]
        for warnings in ([None, " "], None, "private upstream payload"):
            with self.subTest(warnings=warnings), patch.object(recommendation_providers, "search_website", return_value={
                    "results": [], "warnings": warnings, "status": "unavailable"}):
                response = self.client.get("/api/search", params={"q": "nature", "source": "vimeo.com"})
            self.assertEqual(response.status_code, 502)
            self.assertTrue(response.json()["detail"].strip())
            self.assertIn("Vimeo films", response.json()["detail"])
            self.assertNotIn("private upstream", response.json()["detail"])

    def test_deadline_cancels_custom_search(self):
        clock = [0]
        self.settings["providers"] = [website("vimeo.com")]
        def delayed(provider, query, limit, guard):
            clock[0] = manual_video_search.SEARCH_DEADLINE + 1
            guard()
            raise AssertionError("Expired search must stop")
        with patch.object(manual_video_search.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(recommendation_providers, "search_website", side_effect=delayed):
            response = self.client.get("/api/search", params={"q": "nature", "source": "vimeo.com"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("timed out", response.json()["detail"])

    def test_invalid_query_and_limit_are_rejected_before_provider_work(self):
        with patch.object(search, "search_videos") as builtins, patch.object(recommendation_providers, "search_website") as custom:
            for params in ({"q": "x" * 201}, {"q": " "}, {"q": "nature", "limit": 25}, {"q": "nature", "limit": 0}):
                self.assertIn(self.client.get("/api/search", params=params).status_code, (400, 422))
        builtins.assert_not_called()
        custom.assert_not_called()


if __name__ == "__main__":
    unittest.main()
