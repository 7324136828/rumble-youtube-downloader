"""Persistent playback observations and recommendation preferences, no network."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app import config
from app.main import app
from app.services import db
from app.schemas.recommendation_providers import default_providers


class WatchHistoryTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(config, "JOBS_DB_PATH", Path(folder) / "jobs.db"))
        db.init_db()
        self.client = self.enterContext(TestClient(app))
        db.create_video("one", "https://www.youtube.com/watch?v=abcdefghijk", "youtube", "best", folder)
        self.thumbnail = Path(folder) / "one.jpg"
        self.thumbnail.write_bytes(b"thumbnail")
        db.update_video("one", status="ready", title="River wildlife", uploader="Nature channel",
                        duration=120, thumbnail_path=str(self.thumbnail))

    def post(self, **changes):
        return self.client.post("/api/watch-history", json={
            "video_id": "one", "position_seconds": 12, "watched_seconds": 10,
            **changes})

    def test_accumulates_playback_and_preserves_history_after_video_deletion(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post(position_seconds=119, watched_seconds=3, completed=True).status_code, 200)
        db.delete_video("one")
        db.init_db()
        history = self.client.get("/api/watch-history").json()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["watched_seconds"], 13)
        self.assertEqual(history[0]["title"], "River wildlife")
        self.assertEqual(history[0]["position_seconds"], 119)
        self.assertTrue(history[0]["completed"])
        self.assertIsNone(history[0]["media_id"])
        self.assertIsNone(history[0]["thumbnail_url"])
        self.assertNotIn("media_dir", history[0])

        db.create_video("downloaded-again", "https://www.youtube.com/watch?v=abcdefghijk",
                        "youtube", "best", "unused")
        db.update_video("downloaded-again", status="ready", title="River wildlife",
                        thumbnail_path=str(self.thumbnail))
        restored = self.client.get("/api/watch-history").json()[0]
        self.assertEqual(restored["media_id"], "downloaded-again")
        self.assertEqual(restored["thumbnail_url"],
                         "/api/media/downloaded-again/thumbnail")

    def test_history_uses_local_thumbnail_endpoint_without_exposing_its_path(self):
        self.post()
        history = self.client.get("/api/watch-history").json()[0]
        self.assertEqual(history["thumbnail_url"], "/api/media/one/thumbnail")
        self.assertNotIn("thumbnail_path", history)
        thumbnail = self.client.get(history["thumbnail_url"])
        self.assertEqual(thumbnail.status_code, 200)
        self.assertEqual(thumbnail.content, b"thumbnail")

    def test_preloading_without_playback_is_not_watch_history(self):
        self.assertFalse(self.post(watched_seconds=0).json()["recorded"])
        self.assertEqual(self.client.get("/api/watch-history").json(), [])

    def test_seek_position_does_not_inflate_playback_time(self):
        response = self.post(position_seconds=500, watched_seconds=1)
        self.assertEqual(response.status_code, 200)
        history = response.json()["entry"]
        self.assertEqual(history["position_seconds"], 120)
        self.assertEqual(history["watched_seconds"], 1)

    def test_unknown_and_unready_videos_are_rejected(self):
        self.assertEqual(self.post(video_id="missing").status_code, 404)
        db.update_video("one", status="queued")
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(db.list_watch_history(), [])

    def test_untrusted_metadata_and_invalid_observations_are_rejected(self):
        for changes in ({"title": "Fake title"}, {"source_url": "https://invalid.test"},
                        {"position_seconds": -1}, {"watched_seconds": -1},
                        {"watched_seconds": 31}, {"position_seconds": "NaN"},
                        {"watched_seconds": "Infinity"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post(**changes).status_code, 422)
        self.assertEqual(db.list_watch_history(), [])

    def test_recent_history_is_bounded(self):
        db.create_video("two", "https://rumble.com/v123abc-test.html", "rumble", "best", "unused")
        db.update_video("two", status="ready", title="Second video")
        self.post()
        self.post(video_id="two")
        self.assertEqual(self.client.get("/api/watch-history?limit=1").json()[0]["video_id"], "two")
        self.assertEqual(self.client.get("/api/watch-history?limit=101").status_code, 422)

    def test_preferences_default_off_and_survive_reinitialization(self):
        self.assertEqual(db.get_recommendation_settings(), {
            "enabled": False, "model_id": None, "seed_keywords": [], "custom_prompt": "",
            "allow_unverified_links": False, "allow_ai_title_lookup": False,
            "fetch_all_search_links": False, "providers": default_providers(),
            "fallback_weights": {"custom_search": 50, "public_search": 0, "watch_later": 50}, "revision": 0})
        saved = db.update_recommendation_settings({"model_id": "my-recommender", "seed_keywords": ["wildlife"], "enabled": True})
        self.assertEqual(saved["revision"], 1)
        db.init_db()
        self.assertEqual(db.get_recommendation_settings(), saved)
        disabled = db.update_recommendation_settings({"enabled": False})
        self.assertEqual(disabled["revision"], 2)
        self.assertEqual(disabled["model_id"], saved["model_id"])
        self.assertFalse(disabled["enabled"])
        with self.assertRaises(ValueError):
            db.update_recommendation_settings({"other_column": 1})

    def test_stale_settings_update_cannot_undo_a_later_disable(self):
        before = db.get_recommendation_settings()
        db.update_recommendation_settings({"enabled": False})
        with self.assertRaises(db.SettingsConflictError):
            db.update_recommendation_settings({"enabled": True}, expected_revision=before["revision"])
        self.assertFalse(db.get_recommendation_settings()["enabled"])


if __name__ == "__main__":
    unittest.main()
