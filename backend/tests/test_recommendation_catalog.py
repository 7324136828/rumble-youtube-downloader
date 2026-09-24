"""Isolated storage and HTTP contracts for the persistent recommendation catalog."""
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

URL = "https://www.youtube.com/watch?v=abcdefghijk"
VIMEO = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}


def video(**changes):
    return {"source_url": URL, "title": "Discovered title", "description": "Discovered description",
            "source": "youtube", "verified": True, "verification": "provider_search", **changes}


class RecommendationCatalogTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(folder) / "jobs.db"
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.database))
        db.init_db()
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))
        self.providers = default_providers()
        # These catalog tests isolate storage from background metadata fetching.
        self.enterContext(patch("app.services.watch_later_titles.schedule_items", side_effect=lambda items: items))
        self.enterContext(patch("app.services.video_redirects.resolve_import", side_effect=lambda items, providers: items))

    def query(self, sql, params=()):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, params)]

    def save(self, *videos):
        response = self.client.post("/api/recommendations/watch-later", json={"videos": list(videos)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_save_deduplicates_canonical_urls_and_preserves_user_metadata(self):
        saved = self.save({"source_url": "https://youtu.be/abcdefghijk?t=30", "title": "My title"},
                          {"source_url": URL, "title": "My preferred title", "description": "My note"})
        self.assertEqual((saved["added"], saved["updated"], saved["revision"]), (1, 0, 1))
        item = saved["items"][0]
        self.assertEqual(item["source_url"], URL)
        self.assertEqual(item["id"], "youtube:abcdefghijk")
        self.assertEqual(item["connector"], "youtube")
        self.assertFalse(item["verified"])
        self.assertTrue(item["user_added"])
        self.assertEqual(item["origins"], ["watch_later"])
        self.assertEqual(item["user_title"], "My preferred title")
        self.assertEqual(item["user_description"], "My note")
        db.store_discoveries([video()], self.providers)
        found = db.list_catalog_candidates(self.providers)[0]
        self.assertEqual(found["title"], "My preferred title")
        self.assertEqual(found["description"], "My note")
        self.assertTrue(found["verified"])
        self.assertEqual(found["verification"], "provider_search")
        self.assertEqual(set(found["origins"]), {"custom_search", "watch_later"})
        self.assertEqual(found["discovered_count"], 1)
        self.assertEqual(db.get_recommendation_settings()["revision"], 1)
        updated = self.save({"source_url": URL})
        self.assertEqual((updated["added"], updated["updated"]), (0, 1))
        self.assertEqual(updated["items"][0]["title"], "My preferred title")
        self.assertEqual(updated["items"][0]["verification"], "provider_search")

    def test_import_invalid_url_is_atomic_and_rejects_injected_provenance(self):
        for bad in ("https://vimeo.com/123", "https://youtube.com/channel/abc",
                    "https://user:secret@youtube.com/watch?v=abcdefghijk", "https://localhost/video/123"):
            response = self.client.post("/api/recommendations/watch-later", json={"videos": [
                {"source_url": URL}, {"source_url": bad}]})
            self.assertEqual(response.status_code, 400, response.text)
        for extra in ({"verified": True}, {"origins": ["custom_search"]}, {"user_added": False}):
            response = self.client.post("/api/recommendations/watch-later",
                                        json={"videos": [{"source_url": URL, **extra}]})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.query("SELECT * FROM recommendation_videos"), [])
        self.assertEqual(db.get_recommendation_settings()["revision"], 0)

    def test_duplicate_import_retains_explicit_text_when_last_alias_omits_it(self):
        saved = self.save({"source_url": URL, "title": "Personal title", "description": "Personal note"},
                          {"source_url": "https://youtu.be/abcdefghijk"})
        self.assertEqual(saved["added"], 1)
        self.assertEqual(saved["items"][0]["title"], "Personal title")
        self.assertEqual(saved["items"][0]["description"], "Personal note")

    def test_disabled_saved_provider_and_removed_membership_remain_manageable(self):
        providers = self.providers + [{**VIMEO, "enabled": False}]
        db.update_recommendation_settings({"providers": providers})
        saved = self.save({"source_url": "https://vimeo.com/123", "title": "Later"})
        self.assertEqual(db.list_catalog_candidates(providers), [])
        self.assertEqual(self.client.get("/api/recommendations/watch-later?source=vimeo.com").json()["total"], 1)
        db.update_recommendation_settings({"providers": self.providers})
        self.assertEqual(self.client.get("/api/recommendations/watch-later").json()["total"], 1)
        self.assertEqual(self.query("SELECT active, enabled FROM recommendation_websites WHERE id='vimeo.com'"),
                         [{"active": 0, "enabled": 0}])
        response = self.client.delete(f"/api/recommendations/watch-later/{saved['items'][0]['catalog_id']}")
        self.assertTrue(response.json()["removed"])

    def test_remove_only_membership_preserves_discovery_and_is_idempotent(self):
        db.store_discoveries([video()], self.providers)
        saved = self.save({"source_url": URL, "title": "My saved title"})
        catalog_id = saved["items"][0]["catalog_id"]
        db.record_recommendations(saved["items"], self.providers, fallback=True)
        response = self.client.delete(f"/api/recommendations/watch-later/{catalog_id}")
        self.assertEqual(response.json(), {"removed": True, "revision": 2})
        found = db.list_catalog_candidates(self.providers)[0]
        self.assertEqual(found["title"], "Discovered title")
        self.assertTrue(found["verified"])
        self.assertFalse(found["user_added"])
        self.assertEqual(found["origins"], ["custom_search"])
        self.assertEqual(self.client.delete(f"/api/recommendations/watch-later/{catalog_id}").json(),
                         {"removed": False, "revision": 2})
        self.assertEqual(db.list_watch_later()["total"], 0)

    def test_unadorned_save_exposes_no_explicit_metadata_overrides(self):
        saved = self.save({"source_url": URL})["items"][0]
        self.assertEqual(saved["title"], URL)
        self.assertIsNone(saved["user_title"])
        self.assertIsNone(saved["user_description"])
        db.store_discoveries([video()], self.providers)
        discovered = db.list_catalog_candidates(self.providers)[0]
        self.assertEqual(discovered["title"], "Discovered title")
        self.assertIsNone(discovered["user_title"])
        self.assertIsNone(discovered["user_description"])
        db.record_recommendations([discovered], self.providers)
        self.assertEqual(db.list_catalog_candidates(self.providers)[0]["title"], "Discovered title")

    def test_remove_manual_only_video_removes_candidate_eligibility(self):
        item = self.save({"source_url": URL})["items"][0]
        self.assertEqual(len(db.list_catalog_candidates(self.providers)), 1)
        db.record_recommendations([item], self.providers, fallback=True)
        db.remove_watch_later(item["catalog_id"])
        self.assertEqual(db.list_catalog_candidates(self.providers), [])
        self.assertEqual(len(self.query("SELECT * FROM recommendation_history")), 1)

    def test_discovery_and_recommendation_counts_and_origin_are_distinct(self):
        observed = video(verified=False, verification="web_search")
        db.store_discoveries([observed, observed], self.providers)
        db.store_discoveries([observed], self.providers)
        db.record_recommendations([observed, observed], self.providers)
        db.record_recommendations([observed], self.providers, fallback=True)
        item = db.list_catalog_candidates(self.providers)[0]
        self.assertEqual(item["origins"], ["public_search"])
        self.assertFalse(item["verified"])
        self.assertEqual((item["discovered_count"], item["recommended_count"], item["fallback_count"]), (2, 2, 1))
        self.assertIsNotNone(item["last_discovered_at"])
        self.assertIsNotNone(item["last_recommended_at"])
        self.assertEqual(db.get_recommendation_settings()["revision"], 0)
        db.init_db()
        self.assertEqual(db.list_catalog_candidates(self.providers), [item])

    def test_model_only_recommendation_never_becomes_a_discovery(self):
        db.record_recommendations([video(verified=False, verification="model")], self.providers)
        self.assertEqual(db.list_catalog_candidates(self.providers), [])
        self.assertEqual(self.query("SELECT origin FROM recommendation_video_origins"), [{"origin": "model"}])
        self.assertEqual(self.query("SELECT verified, discovered_count, recommended_count FROM recommendation_videos"),
                         [{"verified": 0, "discovered_count": 0, "recommended_count": 1}])

    def test_stale_provider_snapshot_cannot_reactivate_disabled_catalog(self):
        db.store_discoveries([video()], self.providers)
        disabled = [{**provider, "enabled": False} for provider in self.providers]
        db.update_recommendation_settings({"providers": disabled})
        db.store_discoveries([video(title="Stale")], self.providers)
        db.record_recommendations([video()], self.providers)
        self.assertEqual(db.list_catalog_candidates(self.providers), [])
        row = self.query("SELECT title, discovered_count, recommended_count FROM recommendation_videos")[0]
        self.assertEqual(row, {"title": "Discovered title", "discovered_count": 1, "recommended_count": 0})
        db.update_recommendation_settings({"providers": self.providers})
        self.assertEqual(len(db.list_catalog_candidates(self.providers)), 1)

    def test_catalog_cap_preserves_old_saved_and_public_pools(self):
        saved = self.save({"source_url": URL, "title": "Old save"})["items"][0]
        db.store_discoveries([video(source_url="https://www.youtube.com/watch?v=bbbbbbbbbbb",
                                   verified=False, verification="web_search")], self.providers)
        # An old save that was already recommended used to fall behind the
        # entire much larger, not-yet-recommended native discovery catalog.
        db.record_recommendations([saved], self.providers)
        discovered = [video(source_url=f"https://www.youtube.com/watch?v={number:011d}")
                      for number in range(650)]
        db.store_discoveries(discovered, self.providers)
        found = db.list_catalog_candidates(self.providers, limit=600)
        self.assertEqual(len(found), 600)
        self.assertEqual(len({item["catalog_id"] for item in found}), 600)
        self.assertIn(saved["catalog_id"], {item["catalog_id"] for item in found})
        self.assertTrue(any(item["origins"] == ["public_search"] for item in found))
        # A single populated pool still fills the capacity after source removal.
        db.remove_watch_later(saved["catalog_id"])
        native_only = [item for item in db.list_catalog_candidates(self.providers, limit=600)
                       if "custom_search" in item["origins"]]
        self.assertEqual(len(native_only), 599)

    def test_catalog_balances_origins_deduplicates_and_keeps_per_pool_recency(self):
        observed = [video(source_url=f"https://www.youtube.com/watch?v={number:011d}")
                    for number in range(20)]
        db.store_discoveries(observed, self.providers)
        db.store_discoveries([video(source_url="https://www.youtube.com/watch?v=bbbbbbbbbbb",
                                   verified=False, verification="web_search")], self.providers)
        saved = self.save({"source_url": URL}, {"source_url": observed[0]["source_url"]})["items"]
        db.record_recommendations([saved[0]], self.providers)
        found = db.list_catalog_candidates(self.providers, limit=6)
        self.assertEqual(len(found), 6)
        self.assertEqual(len({item["id"] for item in found}), 6)
        watch = [item for item in found if item["user_added"]]
        self.assertEqual([item["catalog_id"] for item in watch], [saved[1]["catalog_id"], saved[0]["catalog_id"]])
        self.assertTrue(any(item["origins"] == ["public_search"] for item in found))
        self.assertEqual(len(db.list_catalog_candidates(self.providers, limit=1)), 1)

    def test_catalog_single_pool_fills_requested_limit(self):
        db.store_discoveries([video(source_url=f"https://www.youtube.com/watch?v={number:011d}")
                              for number in range(15)], self.providers)
        self.assertEqual(len(db.list_catalog_candidates(self.providers, limit=12)), 12)

    def test_weaker_observation_never_downgrades_verified_metadata(self):
        db.store_discoveries([video(duration=30, view_count=14)], self.providers)
        db.store_discoveries([video(title="Untrusted", verified=False, verification="web_search", duration=float("inf"))], self.providers)
        item = db.list_catalog_candidates(self.providers)[0]
        self.assertTrue(item["verified"])
        self.assertEqual(item["title"], "Discovered title")
        self.assertEqual(item["verification"], "provider_search")
        self.assertEqual(item["duration"], 30)
        self.assertEqual(item["view_count"], 14)
        self.assertEqual(set(item["origins"]), {"custom_search", "public_search"})

    def test_pagination_source_filters_and_payload_bounds(self):
        db.update_recommendation_settings({"providers": self.providers + [VIMEO]})
        self.save({"source_url": URL}, {"source_url": "https://vimeo.com/123"}, {"source_url": "https://vimeo.com/456"})
        first = self.client.get("/api/recommendations/watch-later?source=vimeo.com&limit=1").json()
        second = self.client.get("/api/recommendations/watch-later?source=vimeo.com&limit=1&offset=1").json()
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertNotEqual(first["items"][0]["catalog_id"], second["items"][0]["catalog_id"])
        for query in ("limit=0", "limit=201", "offset=-1", "offset=9223372036854775808"):
            self.assertEqual(self.client.get("/api/recommendations/watch-later?" + query).status_code, 422)
        self.assertEqual(self.client.delete("/api/recommendations/watch-later/9223372036854775808").status_code, 422)
        for body in ({"videos": []}, {"videos": [{"source_url": URL}] * 201},
                     {"videos": [{"source_url": URL, "title": "a" * 501}]}):
            self.assertEqual(self.client.post("/api/recommendations/watch-later", json=body).status_code, 422)

    def test_fallback_weights_validate_persist_and_cas(self):
        self.assertEqual(db.get_recommendation_settings()["fallback_weights"], default_fallback_weights())
        weights = {"custom_search": 25, "public_search": 0, "watch_later": 75}
        response = self.client.patch("/api/recommendations/settings", json={"fallback_weights": weights})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["fallback_weights"], weights)
        db.init_db()
        self.assertEqual(db.get_recommendation_settings()["fallback_weights"], weights)
        for bad in (None, {**weights, "custom_search": -1}, {**weights, "watch_later": 70},
                    {**weights, "public_search": True}, {**weights, "watch_later": 75.0},
                    {**weights, "other": 0}):
            response = self.client.patch("/api/recommendations/settings", json={"fallback_weights": bad})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(db.get_recommendation_settings()["fallback_weights"], weights)
        revision = db.get_recommendation_settings()["revision"]
        self.save({"source_url": URL})
        with self.assertRaises(db.SettingsConflictError):
            db.update_recommendation_settings({"fallback_weights": default_fallback_weights()}, expected_revision=revision)
        self.assertEqual(db.get_recommendation_settings()["fallback_weights"], weights)


if __name__ == "__main__":
    unittest.main()
