"""HTTP recommendation synthesis against real temporary SQLite, without network."""
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.recommendations import router
from app.schemas.recommendation_providers import default_providers
from app.services import connector_client, db, recommendation_tools, recommendations


def youtube(ident):
    return "https://www.youtube.com/watch?v=" + ident


SAVED = youtube("aaaaaaaaaaa")
UNTITLED = youtube("bbbbbbbbbbb")
NATIVE = youtube("ccccccccccc")
DIY = "https://vimeo.com/123"
CUSTOM = "https://vimeo.com/222"
DISABLED = "https://rumble.com/v123abc-nature.html"


def observed(url, title, origin="custom_search", verified=True):
    return {"source_url": url, "title": title, "description": "Observed " + title,
            "verified": verified, "origins": [origin],
            "verification": "provider_search" if verified else "custom_search"}


class RecommendationCatalogIntegrationTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "recommendations.sqlite3"
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.path))
        self.enterContext(patch("app.services.watch_later_titles.schedule_items", side_effect=lambda items: items))
        self.enterContext(patch("app.services.video_redirects.resolve_import", side_effect=lambda items, providers: items))
        db.init_db()
        db.update_link_settings({"hide_repeated_links": False})
        recommendations._CACHE.clear()
        recommendations._INFLIGHT.clear()
        self.addCleanup(recommendations._CACHE.clear)
        self.addCleanup(recommendations._INFLIGHT.clear)
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))
        self.enterContext(patch.object(connector_client, "discover_models", return_value={"models": [{"id": "offline-model"}]}))
        self.complete = self.enterContext(patch.object(connector_client, "complete", side_effect=self.model))
        self.search = self.enterContext(patch.object(recommendation_tools, "search_by_key_words", return_value={
            "results": [], "warnings": [], "sources": []}))
        self.providers = [{**provider, "enabled": provider["id"] == "youtube"} for provider in default_providers()]
        self.providers.append({"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True,
                               "search_url": "https://vimeo.com/search?q={query}"})
        self.settings(providers=self.providers, model_id="offline-model", enabled=True,
                      seed_keywords=["wildlife"], allow_unverified_links=True)
        imported = self.client.post("/api/recommendations/watch-later", json={"videos": [
            {"source_url": SAVED, "title": "My wildlife choice", "description": "My personal note"},
            {"source_url": UNTITLED}, {"source_url": DIY, "title": "Watch this later"}]})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.saved_items = {item["source_url"]: item for item in imported.json()["items"]}
        self.observations = [observed(SAVED, "Provider title"), observed(UNTITLED, "Newly discovered title"),
                             observed(NATIVE, "Native video"), observed(CUSTOM, "Custom search video", verified=False),
                             observed(DISABLED, "Disabled website must be filtered")]

    @staticmethod
    def model(model_id, messages, tools=None, **kwargs):
        # Real keyword extraction/ranking code runs; only the Connector boundary
        # is mocked. The empty ranked playlist deliberately triggers fallback.
        payload = {"keywords": ["wildlife"]} if tools is not None else {"playlist": []}
        return {"role": "assistant", "content": json.dumps(payload)}

    def settings(self, **changes):
        response = self.client.patch("/api/recommendations/settings", json=changes)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def generate(self, **request):
        response = self.client.post("/api/recommendations", json={"limit": 20, "refresh": True, **request})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["fallback_used"], result)
        self.assertEqual(result["status"], "ready")
        return result

    def query(self, sql, params=()):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, params)]

    def discover(self):
        # Exercise persistence of all observations, even those beyond the
        # provider's displayed result subset.
        self.search.return_value = {"results": copy.deepcopy(self.observations[:2]),
                                    "all_results": copy.deepcopy(self.observations),
                                    "warnings": [], "sources": []}
        return self.generate()

    def test_api_import_discovery_fallback_and_history_share_persistent_catalog(self):
        revision = db.get_recommendation_settings()["revision"]
        result = self.discover()
        items = {item["source_url"]: item for item in result["items"]}
        self.assertEqual(set(items), {SAVED, UNTITLED, NATIVE, DIY, CUSTOM})
        self.assertEqual(items[SAVED]["title"], "My wildlife choice")
        self.assertEqual(items[SAVED]["description"], "My personal note")
        self.assertEqual(set(items[SAVED]["origins"]), {"custom_search", "watch_later"})
        self.assertEqual(items[UNTITLED]["title"], "Newly discovered title")
        self.assertTrue(items[SAVED]["verified"])
        self.assertFalse(items[DIY]["verified"])
        self.assertFalse(items[CUSTOM]["verified"])
        self.assertEqual({item["fallback_source"] for item in items.values()},
                         {"custom_search", "watch_later"})
        self.assertEqual(db.get_recommendation_settings()["revision"], revision)
        self.assertEqual(self.query("SELECT title, description, user_title, user_description FROM recommendation_videos WHERE source_url=?", (SAVED,)),
                         [{"title": "Provider title", "description": "Observed Provider title",
                           "user_title": "My wildlife choice", "user_description": "My personal note"}])
        self.assertEqual(self.query("SELECT COUNT(*) AS n, SUM(fallback) AS fallbacks FROM recommendation_history"),
                         [{"n": 5, "fallbacks": 5}])
        self.assertEqual(self.query("SELECT COUNT(*) AS n FROM recommendation_videos WHERE source_url=?", (DISABLED,)), [{"n": 0}])
        db.init_db()
        self.search.return_value = {"results": [], "warnings": [], "sources": []}
        persisted = self.generate()
        self.assertEqual({item["source_url"] for item in persisted["items"]}, set(items))
        self.assertEqual(self.query("SELECT COUNT(*) AS n, SUM(fallback) AS fallbacks FROM recommendation_history"),
                         [{"n": 10, "fallbacks": 10}])
        self.assertTrue(all(row["recommended_count"] == 2 for row in
                            self.query("SELECT recommended_count FROM recommendation_videos")))
        response = self.client.delete(f"/api/recommendations/watch-later/{self.saved_items[SAVED]['catalog_id']}")
        self.assertEqual(response.status_code, 200, response.text)
        restored = next(item for item in db.list_catalog_candidates(self.providers) if item["source_url"] == SAVED)
        self.assertEqual(restored["title"], "Provider title")
        self.assertEqual(restored["description"], "Observed Provider title")
        self.assertFalse(restored["user_added"])
        self.assertEqual(restored["recommended_count"], 2)

    def test_persisted_pool_respects_unverified_toggle_disabled_sources_and_exclusions(self):
        self.discover()
        self.search.return_value = {"results": [], "warnings": [], "sources": []}
        self.settings(allow_unverified_links=False)
        result = self.generate()
        items = {item["source_url"]: item for item in result["items"]}
        # Explicit manual saves remain eligible; automatic unverified custom
        # observations require the unverified-link preference.
        self.assertEqual(set(items), {SAVED, UNTITLED, NATIVE, DIY})
        self.assertFalse(items[DIY]["verified"])
        self.assertTrue(items[DIY]["user_added"])
        self.providers[-1]["enabled"] = False
        self.settings(providers=self.providers)
        result = self.generate(source="youtube", exclude_urls=["https://youtu.be/aaaaaaaaaaa?t=30"])
        self.assertEqual({item["source_url"] for item in result["items"]}, {UNTITLED, NATIVE})
        self.assertTrue(all(item["connector"] == "youtube" and item["verified"] for item in result["items"]))
        saved = self.client.get("/api/recommendations/watch-later?source=vimeo.com").json()
        self.assertEqual(saved["total"], 1)
        self.assertEqual(saved["items"][0]["source_url"], DIY)
        self.assertEqual(self.query("SELECT recommended_count FROM recommendation_videos WHERE source_url=?", (DIY,)),
                         [{"recommended_count": 2}])
        before = self.complete.call_count
        self.settings(enabled=False)
        response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.json()["status"], "disabled")
        self.assertEqual(self.complete.call_count, before)

    def test_recommendations_hide_only_links_the_user_marks_repeated(self):
        first = self.discover()
        self.assertTrue(first["items"])
        db.update_link_settings({"hide_repeated_links": True})
        marked = first["items"][0]
        db.update_link_state(marked["link_id"], "silenced")
        recommendations.clear_cache()
        self.search.return_value = {"results": [], "warnings": [], "sources": []}

        response = self.client.post("/api/recommendations", json={"limit": 20, "refresh": True})

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["items"]), len(first["items"]) - 1)
        self.assertNotIn(marked["source_url"], {item["source_url"] for item in result["items"]})
        self.assertTrue(result["fallback_used"])
        history = db.list_link_history()["items"]
        self.assertTrue(history)
        self.assertEqual([row["state"] for row in history].count("silenced"), 1)


if __name__ == "__main__":
    unittest.main()
