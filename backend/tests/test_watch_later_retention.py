"""Saved-video expiration policies, migration, and real thumbnail cleanup."""
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
from app.services import db, watch_later_expiration

NOW = "2030-01-01T12:00:00+00:00"
URL = "https://www.youtube.com/watch?v=abcdefghijk"
OTHER_URL = "https://vimeo.com/123"


class WatchLaterRetentionTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        self.enterContext(patch.object(config, "JOBS_ROOT", self.folder))
        self.clock = self.enterContext(patch.object(db, "_now", return_value=NOW))
        db.init_db()
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def save(self, url=URL, **changes):
        return db.add_watch_later([{"source_url": url, "title": "Saved video", **changes}])["items"][0]

    def change(self, item, days):
        return self.client.patch(f"/api/recommendations/watch-later/{item['catalog_id']}/retention",
                                 json={"retention_days": days})

    def test_default_is_twenty_days_from_save_and_survives_restart(self):
        self.assertEqual(db.get_recommendation_settings()["watch_later_retention_days"], 20)
        item = self.save()
        self.assertEqual(item["retention_days"], 20)
        self.assertEqual(item["retention_started_at"], NOW)
        self.assertEqual(item["expires_at"], "2030-01-21T12:00:00+00:00")
        self.assertFalse(item["retention_override"])
        self.clock.return_value = "2030-01-05T12:00:00+00:00"
        db.init_db()
        self.assertEqual(db.get_watch_later_item(item["catalog_id"]), item)

    def test_per_entry_setting_changes_only_that_membership(self):
        item = self.save()
        sibling = self.save(OTHER_URL)
        settings = db.get_download_settings()
        response = self.change(item, 30)
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["expires_at"], "2030-01-31T12:00:00+00:00")
        self.assertTrue(updated["retention_override"])
        self.assertEqual(db.get_watch_later_item(sibling["catalog_id"]), sibling)
        self.assertEqual(db.get_download_settings(), settings)
        self.assertEqual(db.get_recommendation_settings()["watch_later_retention_days"], 20)
        db.init_db()
        self.assertEqual(db.get_watch_later_item(item["catalog_id"]), updated)

    def test_negative_days_rescue_due_entries_and_defaults_preserve_overrides(self):
        custom = self.save()
        inherited = self.save(OTHER_URL)
        for days in (-1, -30, -(10 ** 50)):
            response = self.change(custom, days)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["retention_days"])
            self.assertIsNone(response.json()["expires_at"])
        response = self.client.patch("/api/recommendations/settings", json={"watch_later_retention_days": 3})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(db.get_watch_later_item(inherited["catalog_id"])["expires_at"], "2030-01-04T12:00:00+00:00")
        self.assertIsNone(db.get_watch_later_item(custom["catalog_id"])["expires_at"])
        newly_saved = self.save("https://archive.org/details/sample")
        self.assertEqual(newly_saved["retention_days"], 3)
        self.assertEqual(watch_later_expiration.purge_expired("2030-01-21T12:00:00+00:00"),
                         [inherited["catalog_id"], newly_saved["catalog_id"]])
        response = self.client.patch("/api/recommendations/settings", json={"watch_later_retention_days": -5})
        self.assertEqual(response.json()["watch_later_retention_days"], -1)
        self.assertIsNone(self.save(OTHER_URL)["expires_at"])

    def test_invalid_policies_are_atomic_and_missing_entry_is_not_found(self):
        item = self.save()
        for days in (0, None, True, "20", 1.5, 3.0, 3651):
            with self.subTest(days=days):
                self.assertEqual(self.change(item, days).status_code, 422)
                self.assertEqual(self.client.patch("/api/recommendations/settings", json={
                    "watch_later_retention_days": days}).status_code, 422)
                self.assertEqual(db.get_watch_later_item(item["catalog_id"]), item)
        self.assertEqual(self.client.patch("/api/recommendations/watch-later/999/retention",
                                          json={"retention_days": 5}).status_code, 404)
        for days in (1, 3650):
            self.assertEqual(self.change(item, days).json()["retention_days"], days)

    def test_resaving_does_not_extend_countdown_and_import_can_set_override(self):
        item = self.save(retention_days=7)
        self.assertTrue(item["retention_override"])
        self.clock.return_value = "2030-01-06T12:00:00+00:00"
        resaved = self.save()
        self.assertEqual(resaved["expires_at"], item["expires_at"])
        self.assertEqual(resaved["retention_days"], 7)
        response = self.client.post("/api/recommendations/watch-later", json={
            "videos": [{"source_url": OTHER_URL, "retention_days": 0}],
            "fetch_titles": False, "resolve_redirects": False})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(db.list_watch_later()["total"], 1)
        db.remove_watch_later(item["catalog_id"])
        new = self.save()
        self.assertFalse(new["retention_override"])
        self.assertEqual(new["expires_at"], "2030-01-26T12:00:00+00:00")

    def test_cleanup_removes_only_membership_and_artwork_and_retires_workers(self):
        db.store_discoveries([{"source_url": URL, "title": "Original discovery",
            "verified": True, "verification": "provider_search"}], db.get_recommendation_settings()["providers"])
        item = self.save()
        root = self.folder / "watch_later_thumbnails"
        root.mkdir()
        thumbnail = root / f"{item['catalog_id']}.jpg"
        thumbnail.write_bytes(b"image")
        db.begin_watch_later_thumbnail(item["catalog_id"], "artwork")
        db.finish_watch_later_thumbnail(item["catalog_id"], "artwork", str(thumbnail))
        db.begin_watch_later_title(item["catalog_id"], "title-worker", force=True)
        media_dir = self.folder / "download"
        media_dir.mkdir()
        media_file = media_dir / "video.mp4"
        media_file.write_bytes(b"video")
        db.create_video("download", URL, "youtube", "best", media_dir, None)
        db.update_video("download", status="ready", file_path=str(media_file), title="Downloaded copy")
        db.record_watch("download", 5, 5)
        history = db.list_watch_history()
        downloaded = db.get_video("download")
        self.assertEqual(watch_later_expiration.purge_expired("2030-01-21T11:59:59+00:00"), [])
        self.assertTrue(thumbnail.exists())
        self.assertEqual(watch_later_expiration.purge_expired("2030-01-21T12:00:00+00:00"), [item["catalog_id"]])
        self.assertFalse(thumbnail.exists())
        self.assertTrue(media_file.exists())
        self.assertEqual(db.get_video("download"), downloaded)
        self.assertEqual(db.list_watch_history(), history)
        self.assertIsNone(db.get_watch_later_item(item["catalog_id"]))
        self.assertFalse(db.finish_watch_later_title(item["catalog_id"], "title-worker", "Late title"))
        remaining = db.list_catalog_candidates(db.get_recommendation_settings()["providers"])[0]
        self.assertEqual(remaining["title"], "Original discovery")
        self.assertIn("custom_search", remaining["origins"])
        self.assertNotIn("watch_later", remaining["origins"])

    def test_list_request_cleans_due_entries_even_when_ai_is_off(self):
        self.save()
        kept = self.save(OTHER_URL, retention_days=-1)
        self.clock.return_value = "2030-01-22T12:00:00+00:00"
        result = self.client.get("/api/recommendations/watch-later").json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["catalog_id"], kept["catalog_id"])
        self.assertFalse(db.get_recommendation_settings()["enabled"])

    def test_legacy_entries_start_fresh_once_without_changing_original_save_date(self):
        self.clock.return_value = "2000-01-01T12:00:00+00:00"
        item = self.save()
        with closing(sqlite3.connect(config.JOBS_DB_PATH)) as connection:
            connection.execute("DROP INDEX watch_later_expiration")
            for column in ("retention_days", "retention_override", "retention_started_at", "expires_at"):
                connection.execute(f"ALTER TABLE recommendation_video_origins DROP COLUMN {column}")
            connection.execute("ALTER TABLE recommendation_settings DROP COLUMN watch_later_retention_days")
            connection.commit()
        self.clock.return_value = NOW
        db.init_db()
        migrated = db.get_watch_later_item(item["catalog_id"])
        self.assertEqual(migrated["saved_at"], "2000-01-01T12:00:00+00:00")
        self.assertEqual(migrated["retention_started_at"], NOW)
        self.assertEqual(migrated["expires_at"], "2030-01-21T12:00:00+00:00")
        self.clock.return_value = "2030-01-06T12:00:00+00:00"
        db.init_db()
        self.assertEqual(db.get_watch_later_item(item["catalog_id"]), migrated)


if __name__ == "__main__":
    unittest.main()
