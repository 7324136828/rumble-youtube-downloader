"""Per-video retention policies, using isolated SQLite and local fixture files."""
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app import config
from app.connectors import DownloadResult, VideoInfo
from app.main import app
from app.services import db, library


class VideoRetentionTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        db.init_db()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create_video(self, video_id="video", status="ready", retention_days=7):
        media_dir = self.folder / video_id
        media_dir.mkdir()
        path = media_dir / "video.mp4"
        path.write_bytes(b"fixture video")
        db.create_video(video_id, f"https://example.com/{video_id}", "generic", "best",
                        media_dir, retention_days)
        db.update_video(video_id, status=status, title="A saved video", file_path=str(path),
                        created_at="2020-01-01T00:00:00+00:00",
                        completed_at="2020-02-01T12:00:00+00:00" if status == "ready" else None)
        return db.get_video(video_id)

    def test_positive_days_recalculate_from_download_date_for_only_this_video(self):
        self.create_video()
        other = self.create_video("other")
        settings = db.get_download_settings()
        response = self.client.patch("/api/media/video/retention", json={"retention_days": 30})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["retention_days"], 30)
        self.assertEqual(body["expires_at"], "2020-03-02T12:00:00+00:00")
        self.assertTrue(body["retention_override"])
        self.assertEqual(body["stream_url"], "/api/media/video/stream")
        self.assertNotIn("media_dir", body)
        self.assertNotIn("file_path", body)
        self.assertEqual(db.get_video("other"), other)
        self.assertEqual(db.get_download_settings(), settings)

    def test_negative_days_rescue_expired_video_without_pre_update_purge(self):
        video = self.create_video()
        for days in (-1, -30, -(10 ** 50)):
            with self.subTest(days=days):
                response = self.client.patch("/api/media/video/retention", json={"retention_days": days})
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()["retention_days"])
                self.assertIsNone(response.json()["expires_at"])
        self.assertEqual(library.purge_expired(), [])
        self.assertTrue(Path(video["file_path"]).is_file())

    def test_strict_required_integer_validation_leaves_existing_policy_unchanged(self):
        original = self.create_video()
        invalid_bodies = [{}, {"retention_days": 4, "other": True}]
        invalid_bodies += [{"retention_days": value} for value in
                           (0, True, False, 1.5, 3.0, "7", "-1", None, 3651)]
        for body in invalid_bodies:
            with self.subTest(body=body):
                response = self.client.patch("/api/media/video/retention", json=body)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(db.get_video("video"), original)

    def test_positive_boundaries_and_missing_video(self):
        self.create_video(status="queued")
        for days in (1, 3650):
            response = self.client.patch("/api/media/video/retention", json={"retention_days": days})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["retention_days"], days)
        response = self.client.patch("/api/media/missing/retention", json={"retention_days": -1})
        self.assertEqual(response.status_code, 404)

    def test_active_downloads_have_no_deadline_until_completion(self):
        for status in ("queued", "downloading", "processing", "failed"):
            with self.subTest(status=status):
                self.create_video(status, status=status)
                row = library.update_video_retention(status, 3)
                self.assertIsNone(row["expires_at"])
                db.complete_video(status, "2026-01-20T10:30:00+00:00", progress=100)
                ready = db.get_video(status)
                self.assertEqual(ready["expires_at"], "2026-01-23T10:30:00+00:00")
                self.assertEqual(ready["retention_days"], 3)

    def test_overrides_survive_restart_and_global_policy_changes(self):
        self.create_video("inherited", status="queued")
        self.create_video("custom", status="queued")
        self.create_video("forever", status="queued")
        library.update_video_retention("custom", 2)
        library.update_video_retention("forever", -10)
        db.init_db()
        for days in (30, -1, 7):
            db.update_download_settings({"retention_days": days})
            self.assertEqual(db.get_video("inherited")["retention_days"], days if days > 0 else None)
            self.assertEqual(db.get_video("custom")["retention_days"], 2)
            self.assertIsNone(db.get_video("forever")["retention_days"])
            self.assertFalse(db.get_video("inherited")["retention_override"])
            self.assertTrue(db.get_video("forever")["retention_override"])

    def test_existing_database_migration_preserves_media_and_inherits_defaults(self):
        existing = self.folder / "old.db"
        with patch.object(config, "JOBS_DB_PATH", existing):
            with closing(sqlite3.connect(existing)) as conn:
                conn.executescript(db._SCHEMA.replace("    retention_override INTEGER NOT NULL DEFAULT 0,\n", ""))
            self.create_video(status="queued")
            db.init_db()
            db.init_db()
            row = db.get_video("video")
            self.assertFalse(row["retention_override"])
            self.assertEqual(row["retention_days"], 7)
            self.assertEqual(row["title"], "A saved video")
            db.update_download_settings({"retention_days": 14})
            self.assertEqual(db.get_video("video")["retention_days"], 14)

    def test_download_worker_uses_policy_changed_just_before_completion(self):
        row = self.create_video(status="queued")
        connector = SimpleNamespace(download=lambda *args, **kwargs: DownloadResult(
            path=Path(row["file_path"]), info=VideoInfo(title="Downloaded title")))
        complete = db.complete_video

        def keep_then_complete(*args, **kwargs):
            library.update_video_retention("video", -1)
            return complete(*args, **kwargs)

        with patch.object(library.media, "is_browser_compatible", return_value=True), \
                patch.object(db, "complete_video", side_effect=keep_then_complete), \
                patch.object(library, "_remove_downloaded_from_watch_later"):
            library._run("video", row["source_url"], "best", connector, Path(row["media_dir"]),
                         threading.Event(), {"convert_for_browser": False, "generate_thumbnails": False})
        ready = db.get_video("video")
        self.assertEqual(ready["status"], "ready")
        self.assertIsNone(ready["retention_days"])
        self.assertIsNone(ready["expires_at"])

    def test_completion_and_policy_edit_are_atomic(self):
        self.create_video(status="processing")
        entered, release = threading.Event(), threading.Event()
        expiration = db._video_expiration

        def paused_completion(video, days):
            if video["status"] == "ready" and days == 7:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Completion was not released")
            return expiration(video, days)

        with patch.object(db, "_video_expiration", side_effect=paused_completion), \
                ThreadPoolExecutor(max_workers=2) as pool:
            completion = pool.submit(db.complete_video, "video", "2026-01-20T10:30:00+00:00")
            self.assertTrue(entered.wait(5))
            update = pool.submit(library.update_video_retention, "video", -1)
            release.set()
            completion.result(timeout=5)
            self.assertIsNone(update.result(timeout=5)["expires_at"])
        ready = db.get_video("video")
        self.assertEqual(ready["status"], "ready")
        self.assertIsNone(ready["expires_at"])

    def test_successful_keep_update_is_protected_from_overlapping_purge(self):
        row = self.create_video()
        entered, release = threading.Event(), threading.Event()
        expiration = db._video_expiration

        def paused_update(video, days):
            if days is None:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Retention update was not released")
            return expiration(video, days)

        with patch.object(db, "_video_expiration", side_effect=paused_update), \
                ThreadPoolExecutor(max_workers=2) as pool:
            update = pool.submit(library.update_video_retention, "video", -1)
            self.assertTrue(entered.wait(5))
            cleanup = pool.submit(library.purge_expired)
            release.set()
            self.assertIsNone(update.result(timeout=5)["retention_days"])
            self.assertEqual(cleanup.result(timeout=5), [])
        self.assertIsNotNone(db.get_video("video"))
        self.assertTrue(Path(row["file_path"]).is_file())

    def test_purge_cannot_delete_from_stale_candidates_after_successful_update(self):
        self.create_video()
        selected, release, attempted, finished = (threading.Event() for _ in range(4))
        expired_videos = db.list_expired_videos

        def paused_selection():
            candidates = expired_videos()
            selected.set()
            if not release.wait(5):
                raise TimeoutError("Cleanup was not released")
            return candidates

        def update_policy():
            attempted.set()
            try:
                return library.update_video_retention("video", -1)
            finally:
                finished.set()

        with patch.object(db, "list_expired_videos", side_effect=paused_selection), \
                ThreadPoolExecutor(max_workers=2) as pool:
            cleanup = pool.submit(library.purge_expired)
            self.assertTrue(selected.wait(5))
            update = pool.submit(update_policy)
            self.assertTrue(attempted.wait(5))
            try:
                self.assertFalse(finished.wait(0.05))
            finally:
                release.set()
            self.assertEqual(cleanup.result(timeout=5), ["video"])
            with self.assertRaises(LookupError):
                update.result(timeout=5)


if __name__ == "__main__":
    unittest.main()
