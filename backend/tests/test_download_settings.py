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
    DEFAULTS = {"convert_for_browser": False, "generate_thumbnails": True,
                "auto_delete_enabled": True, "retention_days": 7,
                "cookie_browser": "", "cookie_browser_profile": "", "cookie_file": ""}
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        db.init_db()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_new_installations_preserve_original_formats_by_default(self):
        response = self.client.get("/api/settings/downloads")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), self.DEFAULTS)

    def test_partial_changes_persist_without_affecting_other_settings(self):
        first = self.client.patch("/api/settings/downloads", json={"convert_for_browser": True})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {**self.DEFAULTS, "convert_for_browser": True})
        db.init_db()
        second = self.client.patch("/api/settings/downloads", json={"generate_thumbnails": False})
        self.assertEqual(second.json(), {**self.DEFAULTS, "convert_for_browser": True,
                                         "generate_thumbnails": False})
        self.assertEqual(self.client.patch("/api/settings/downloads", json={}).json(), second.json())
        self.assertEqual(self.client.get("/api/settings/downloads").json(), second.json())
        self.assertFalse(db.get_recommendation_settings()["enabled"])

    def test_invalid_preferences_do_not_change_saved_values(self):
        for body in ({"convert_for_browser": "false"}, {"generate_thumbnails": 0},
                     {"convert_for_browser": None}, {"retention_days": 0},
                     {"retention_days": True}, {"retention_days": 3651},
                     {"cookie_browser": "netscape"}, {"cookie_browser": None},
                     {"cookie_browser_profile": "Default\x00"},
                     {"cookie_browser_profile": "Default"},
                     {"cookie_file": None}, {"cookie_file": 123},
                     {"cookie_file": "cookies.txt"}, {"cookie_file": "../cookies.txt"},
                     {"cookie_file": "https://example.com/cookies.txt"},
                     {"cookie_file": str(self.folder / "cookies.txt") + "\n"},
                     {"cookie_file": str(self.folder / "cookies.txt") + "\x7f"},
                     {"cookie_file": "x" * 2049},
                     {"unrecognized": True}, {"conversion_opt_in": True}):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch("/api/settings/downloads", json=body).status_code, 422)
        self.assertEqual(db.get_download_settings(), self.DEFAULTS)

    def test_browser_cookie_choice_and_profile_persist_without_cookie_values(self):
        response = self.client.patch("/api/settings/downloads", json={
            "cookie_browser": "edge", "cookie_browser_profile": "  Profile 2  "})
        self.assertEqual(response.status_code, 200)
        saved = response.json()
        self.assertEqual(saved["cookie_browser"], "edge")
        self.assertEqual(saved["cookie_browser_profile"], "Profile 2")
        self.assertNotIn("cookiefile", saved)
        db.init_db()
        self.assertEqual(db.get_download_settings(), saved)
        cleared = self.client.patch("/api/settings/downloads", json={"cookie_browser": ""}).json()
        self.assertEqual(cleared["cookie_browser_profile"], "")

    def test_profile_only_patch_uses_saved_browser(self):
        self.client.patch("/api/settings/downloads", json={"cookie_browser": "edge"})
        response = self.client.patch("/api/settings/downloads", json={
            "cookie_browser_profile": "  Profile 2  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cookie_browser"], "edge")
        self.assertEqual(response.json()["cookie_browser_profile"], "Profile 2")

    def test_cookie_file_path_persists_without_reading_file_or_clearing_browser(self):
        cookie_path = self.folder / "exported cookies.txt"
        self.assertFalse(cookie_path.exists())
        self.client.patch("/api/settings/downloads", json={
            "cookie_browser": "edge", "cookie_browser_profile": "Profile 2"})
        response = self.client.patch("/api/settings/downloads", json={
            "cookie_file": f'  "{cookie_path}"  '})
        self.assertEqual(response.status_code, 200)
        saved = response.json()
        self.assertEqual(saved["cookie_file"], str(cookie_path))
        self.assertEqual(saved["cookie_browser"], "edge")
        self.assertEqual(saved["cookie_browser_profile"], "Profile 2")
        db.init_db()
        self.assertEqual(db.get_download_settings(), saved)
        response = self.client.patch("/api/settings/downloads", json={"generate_thumbnails": False})
        self.assertEqual(response.json(), {**saved, "generate_thumbnails": False})
        cleared = self.client.patch("/api/settings/downloads", json={"cookie_file": "  "}).json()
        self.assertEqual(cleared["cookie_file"], "")
        self.assertEqual(cleared["cookie_browser"], "edge")
        self.assertEqual(cleared["cookie_browser_profile"], "Profile 2")

    def test_clearing_browser_does_not_clear_saved_cookie_file(self):
        cookie_path = str(self.folder / "cookies.txt")
        self.client.patch("/api/settings/downloads", json={
            "cookie_browser": "edge", "cookie_browser_profile": "Profile 2",
            "cookie_file": cookie_path})
        response = self.client.patch("/api/settings/downloads", json={"cookie_browser": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cookie_browser_profile"], "")
        self.assertEqual(response.json()["cookie_file"], cookie_path)

    def test_database_rejects_invalid_cookie_preferences_atomically(self):
        for invalid in ({"cookie_file": []}, {"cookie_file": "cookies.txt"},
                        {"cookie_file": str(self.folder / "cookies.txt") + "\x00"},
                        {"cookie_file": "x" * 2049}, {"cookie_browser": []},
                        {"cookie_browser_profile": "Default"}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    db.update_download_settings({"generate_thumbnails": False, **invalid})
                self.assertEqual(db.get_download_settings(), self.DEFAULTS)

    def test_existing_browser_settings_gain_cookie_file_without_losing_choices(self):
        old_database = self.folder / "browser-settings.db"
        with closing(sqlite3.connect(old_database)) as conn:
            conn.executescript("""
                CREATE TABLE download_settings (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    convert_for_browser INTEGER NOT NULL DEFAULT 0,
                    conversion_opt_in INTEGER NOT NULL DEFAULT 0,
                    generate_thumbnails INTEGER NOT NULL DEFAULT 1,
                    auto_delete_enabled INTEGER NOT NULL DEFAULT 1,
                    retention_days INTEGER NOT NULL DEFAULT 7,
                    cookie_browser TEXT NOT NULL DEFAULT '',
                    cookie_browser_profile TEXT NOT NULL DEFAULT '');
                INSERT INTO download_settings (id, cookie_browser, cookie_browser_profile)
                    VALUES (1, 'edge', 'Profile 2');
            """)
        with patch.object(config, "JOBS_DB_PATH", old_database):
            db.init_db()
            db.init_db()
            self.assertEqual(db.get_download_settings(), {
                **self.DEFAULTS, "cookie_browser": "edge", "cookie_browser_profile": "Profile 2"})

    def test_changing_thumbnails_does_not_enable_conversion(self):
        response = self.client.patch("/api/settings/downloads", json={"generate_thumbnails": False})
        self.assertEqual(response.json(), {**self.DEFAULTS, "generate_thumbnails": False})
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
            self.assertEqual(db.get_download_settings(), {**self.DEFAULTS, "generate_thumbnails": False})
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
            self.assertIsNone(row["retention_days"])
            self.assertIsNone(row["expires_at"])

    def test_retention_and_keep_indefinitely_choices_persist(self):
        saved = self.client.patch("/api/settings/downloads", json={
            "retention_days": -30}).json()
        self.assertFalse(saved["auto_delete_enabled"])
        self.assertEqual(saved["retention_days"], -1)
        db.init_db()
        self.assertEqual(self.client.get("/api/settings/downloads").json(), saved)

    def test_changing_retention_recalculates_existing_downloads_from_completion(self):
        media_dir = self.folder / "ready"
        media_dir.mkdir()
        db.create_video("recent", "https://example.com/recent", "generic", "best", media_dir, 30)
        db.update_video("recent", status="ready", completed_at="2026-09-20T12:00:00+00:00")
        with patch("app.routers.settings.library.purge_expired") as purge:
            response = self.client.patch("/api/settings/downloads", json={"retention_days": 3})
        self.assertEqual(response.status_code, 200)
        row = db.get_video("recent")
        self.assertEqual(row["retention_days"], 3)
        self.assertEqual(row["expires_at"], "2026-09-23T12:00:00+00:00")
        purge.assert_called_once()

        self.client.patch("/api/settings/downloads", json={"retention_days": -5})
        row = db.get_video("recent")
        self.assertIsNone(row["retention_days"])
        self.assertIsNone(row["expires_at"])


if __name__ == "__main__":
    unittest.main()
