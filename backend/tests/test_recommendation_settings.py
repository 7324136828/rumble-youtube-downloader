"""Custom website preferences persist, migrate, and validate at the API boundary."""
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
from app.schemas.recommendations import default_fallback_weights
from app.services import db


class RecommendationSettingsTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(folder) / "jobs.db"
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.database))
        db.init_db()
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def test_add_disable_remove_and_reload_websites(self):
        providers = default_providers() + [
            {"name": "Vimeo", "domain": "https://www.Vimeo.com/", "enabled": True,
             "thumbnail_domains": ["i.vimeocdn.com"]}]
        response = self.client.patch("/api/recommendations/settings", json={"providers": providers})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["providers"][-1], {
            "id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True,
            "thumbnail_domains": ["i.vimeocdn.com"]})
        self.assertEqual(saved["revision"], 1)
        db.init_db()
        self.assertEqual(self.client.get("/api/recommendations/settings").json(), saved)
        providers = saved["providers"]
        providers[0]["enabled"] = False
        response = self.client.patch("/api/recommendations/settings", json={"providers": providers})
        self.assertFalse(response.json()["providers"][0]["enabled"])
        response = self.client.patch("/api/recommendations/settings", json={"providers": providers[:-1]})
        self.assertEqual(len(response.json()["providers"]), 2)
        self.assertEqual(response.json()["revision"], 3)

    def test_invalid_websites_leave_preferences_unchanged(self):
        before = db.get_recommendation_settings()
        cases = [None, default_providers() * 2,
                 [{"name": "Vimeo", "domain": "vimeo.com"},
                  {"name": "Vimeo player", "domain": "player.vimeo.com"}],
                 [{"name": "Private", "domain": "127.0.0.1"}],
                 [{"name": "Private", "domain": "localhost"}],
                 [{"name": "Wrong ID", "domain": "vimeo.com", "id": "youtube"}],
                 [{"name": "Login", "domain": "https://user:password@vimeo.com"}],
                 [{"name": "Path", "domain": "vimeo.com/search"}],
                 [{"name": "Vimeo", "domain": "vimeo.com", "enabled": "true"}],
                 [{"name": "Vimeo", "domain": "vimeo.com", "thumbnail_domains": ["localhost"]}],
                 [{"name": "Vimeo", "domain": "vimeo.com", "thumbnail_domains": ["cdn.example.com"] * 2}],
                 [{"name": "Vimeo", "domain": "vimeo.com", "thumbnail_domains": [f"cdn{n}.com" for n in range(9)]}],
                 [{"name": f"Site {n}", "domain": f"site{n}.com"} for n in range(13)]]
        for providers in cases:
            with self.subTest(providers=providers):
                response = self.client.patch("/api/recommendations/settings", json={"providers": providers})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(db.get_recommendation_settings(), before)

    def test_empty_websites_remains_empty_after_restart(self):
        self.client.patch("/api/recommendations/settings", json={"providers": []})
        db.init_db()
        self.assertEqual(db.get_recommendation_settings()["providers"], [])

    def test_fetch_all_search_links_toggle_persists(self):
        response = self.client.patch("/api/recommendations/settings", json={
            "fetch_all_search_links": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["fetch_all_search_links"])
        db.init_db()
        self.assertTrue(self.client.get("/api/recommendations/settings").json()[
            "fetch_all_search_links"])
        self.assertEqual(self.client.patch("/api/recommendations/settings", json={
            "fetch_all_search_links": None}).status_code, 422)

    def test_custom_prompt_can_be_changed_or_cleared(self):
        response = self.client.patch("/api/recommendations/settings", json={
            "custom_prompt": "  Prefer short tutorials and new creators.  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["custom_prompt"],
                         "Prefer short tutorials and new creators.")
        db.init_db()
        self.assertEqual(db.get_recommendation_settings()["custom_prompt"],
                         "Prefer short tutorials and new creators.")
        cleared = self.client.patch("/api/recommendations/settings", json={
            "custom_prompt": ""})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["custom_prompt"], "")

    def test_thumbnail_domain_detection_only_suggests_for_configured_custom_website(self):
        providers = default_providers() + [{
            "name": "Vimeo", "domain": "vimeo.com", "enabled": False,
            "search_url": "https://vimeo.com/search?q={query}"}]
        self.client.patch("/api/recommendations/settings", json={"providers": providers})
        with patch("app.services.custom_website_search.discover_thumbnail_domains",
                   return_value={"domains": ["i.vimeocdn.com"],
                                 "page_url": "https://vimeo.com/search?q=video"}) as detect:
            response = self.client.post(
                "/api/recommendations/providers/vimeo.com/thumbnail-domains/detect")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["domains"], ["i.vimeocdn.com"])
        self.assertFalse(detect.call_args.args[0]["enabled"])
        self.assertEqual(self.client.post(
            "/api/recommendations/providers/youtube/thumbnail-domains/detect").status_code, 404)
        self.assertEqual(self.client.post(
            "/api/recommendations/providers/missing.com/thumbnail-domains/detect").status_code, 404)

    def test_legacy_settings_migrate_without_losing_user_choices(self):
        legacy_path = self.database.with_name("legacy.db")
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript("""
                CREATE TABLE recommendation_settings (
                    id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                    model_id TEXT, seed_keywords TEXT NOT NULL DEFAULT '[]',
                    allow_unverified_links INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0);
                INSERT INTO recommendation_settings VALUES
                    (1, 1, 'saved-model', '["wildlife"]', 1, 7);
            """)
        with patch.object(config, "JOBS_DB_PATH", legacy_path):
            db.init_db()
            settings = db.get_recommendation_settings()
            self.assertEqual(settings, {"enabled": True, "model_id": "saved-model",
                "seed_keywords": ["wildlife"], "custom_prompt": "", "allow_unverified_links": True, "allow_ai_title_lookup": False,
                "fetch_all_search_links": False,
                "providers": default_providers(), "fallback_weights": default_fallback_weights(), "revision": 7})
            settings["providers"][0]["enabled"] = False
            updated = db.update_recommendation_settings({"providers": settings["providers"]})
            db.init_db()
            self.assertEqual(db.get_recommendation_settings(), updated)


if __name__ == "__main__":
    unittest.main()
