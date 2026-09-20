"""Download preference API and existing-library migration, using isolated SQLite."""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app import config
from app.main import app
from app.services import db


class DownloadSettingsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        db.init_db()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_new_installations_preserve_original_formats_by_default(self):
        response = self.client.get("/api/settings/downloads")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"convert_for_browser": False, "generate_thumbnails": True})

    def test_partial_changes_persist_without_affecting_other_settings(self):
        first = self.client.patch("/api/settings/downloads", json={"convert_for_browser": True})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"convert_for_browser": True, "generate_thumbnails": True})
        db.init_db()
        second = self.client.patch("/api/settings/downloads", json={"generate_thumbnails": False})
        self.assertEqual(second.json(), {"convert_for_browser": True, "generate_thumbnails": False})
        self.assertEqual(self.client.patch("/api/settings/downloads", json={}).json(), second.json())
        self.assertEqual(self.client.get("/api/settings/downloads").json(), second.json())
        self.assertFalse(db.get_recommendation_settings()["enabled"])

    def test_invalid_preferences_do_not_change_saved_values(self):
        for body in ({"convert_for_browser": "false"}, {"generate_thumbnails": 0},
                     {"convert_for_browser": None}, {"unrecognized": True}, {"conversion_opt_in": True}):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch("/api/settings/downloads", json=body).status_code, 422)
        self.assertEqual(db.get_download_settings(), {"convert_for_browser": False, "generate_thumbnails": True})

    def test_changing_thumbnails_does_not_enable_conversion(self):
        response = self.client.patch("/api/settings/downloads", json={"generate_thumbnails": False})
        self.assertEqual(response.json(), {"convert_for_browser": False, "generate_thumbnails": False})
        db.init_db()
        self.assertFalse(db.get_download_settings()["convert_for_browser"])

    def test_old_default_on_requires_opt_in_and_later_choices_survive_restart(self):
        old_database = self.folder / "old-settings.db"
        with closing(sqlite3.connect(old_database)) as conn:
            conn.executescript("""
                CREATE TABLE download_settings (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    convert_for_browser INTEGER NOT NULL DEFAULT 1,
                    generate_thumbnails INTEGER NOT NULL DEFAULT 1);
                INSERT INTO download_settings (id, generate_thumbnails) VALUES (1, 0);
            """)
        with patch.object(config, "JOBS_DB_PATH", old_database):
            db.init_db()
            self.assertEqual(db.get_download_settings(), {"convert_for_browser": False, "generate_thumbnails": False})
            db.update_download_settings({"convert_for_browser": True})
            db.init_db()
            self.assertTrue(db.get_download_settings()["convert_for_browser"])
            db.update_download_settings({"convert_for_browser": False})
            db.init_db()
            self.assertFalse(db.get_download_settings()["convert_for_browser"])

    def test_existing_video_rows_gain_nullable_warning_without_data_loss(self):
        existing = self.folder / "existing.db"
        with patch.object(config, "JOBS_DB_PATH", existing):
            with closing(sqlite3.connect(existing)) as conn:
                conn.executescript(db._SCHEMA.replace("    playback_warning TEXT,\n", ""))
            db.create_video("saved", "https://example.com/video", "generic", "best", self.folder)
            db.update_video("saved", title="Previously saved video", status="ready", file_path="original.webm")
            db.init_db()
            db.init_db()
            row = db.get_video("saved")
            self.assertEqual(row["title"], "Previously saved video")
            self.assertEqual(row["status"], "ready")
            self.assertEqual(row["file_path"], "original.webm")
            self.assertIsNone(row["playback_warning"])


if __name__ == "__main__":
    unittest.main()
