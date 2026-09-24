"""Automatic local thumbnails for Watch later videos."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.recommendations import router
from app.services import db, library, watch_later_thumbnails as thumbnails

URL = "https://www.youtube.com/watch?v=abcdefghijk"


class WatchLaterThumbnailTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.root / "jobs.db"))
        self.enterContext(patch.object(config, "JOBS_ROOT", self.root / "jobs"))
        db.init_db()
        self.item = db.add_watch_later([{"source_url": URL, "title": "Saved title"}])["items"][0]
        thumbnails._PENDING.clear()
        thumbnails._POOL = None
        self.addCleanup(thumbnails.shutdown)

    def run_job(self, content=b"jpeg thumbnail"):
        token = "thumbnail-token"
        claimed = db.begin_watch_later_thumbnail(self.item["catalog_id"], token)
        key = (str(config.JOBS_DB_PATH.resolve()), self.item["catalog_id"])
        thumbnails._PENDING[token] = key

        def run(command, **kwargs):
            template = Path(command[command.index("--output") + 1])
            template.with_name(template.name.replace("%(ext)s", "jpg")).write_bytes(content)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(thumbnails.subprocess, "run", side_effect=run) as process:
            thumbnails._run(key, token, claimed)
        return process

    def test_downloads_thumbnail_only_and_persists_local_endpoint(self):
        process = self.run_job()
        command = process.call_args.args[0]
        self.assertIn("--skip-download", command)
        self.assertIn("--write-thumbnail", command)
        self.assertIn("--no-playlist", command)
        item = db.get_watch_later_item(self.item["catalog_id"])
        self.assertEqual(item["thumbnail_fetch_status"], "ready")
        self.assertEqual(item["thumbnail_url"], f"/api/recommendations/watch-later/{self.item['catalog_id']}/thumbnail")
        path = Path(db.get_watch_later_thumbnail(self.item["catalog_id"]))
        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to((config.JOBS_ROOT / "watch_later_thumbnails").resolve()))

    def test_schedule_is_bounded_and_does_not_replace_existing_thumbnail(self):
        pool = MagicMock()
        thumbnails._POOL = pool
        result = thumbnails.schedule_items([self.item])[0]
        self.assertEqual(result["thumbnail_fetch_status"], "pending")
        pool.submit.assert_called_once()
        duplicate = thumbnails.request_thumbnail(self.item["catalog_id"])
        self.assertTrue(duplicate["queued"])
        self.assertEqual(pool.submit.call_count, 1)

    def test_thumbnail_endpoint_serves_only_catalog_thumbnail_path(self):
        self.run_job()
        thumbnail = Path(db.get_watch_later_thumbnail(self.item["catalog_id"]))
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get(f"/api/recommendations/watch-later/{self.item['catalog_id']}/thumbnail")
            missing = client.get("/api/recommendations/watch-later/999/thumbnail")
            removed = client.delete(f"/api/recommendations/watch-later/{self.item['catalog_id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"jpeg thumbnail")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(removed.json()["removed"], True)
        self.assertFalse(thumbnail.exists())

    def test_save_queues_thumbnail_even_when_title_fetch_is_disabled(self):
        app = FastAPI()
        app.include_router(router)
        with patch.object(thumbnails, "schedule_items", side_effect=lambda items: items) as schedule:
            with TestClient(app) as client:
                response = client.post("/api/recommendations/watch-later", json={
                    "videos": [{"source_url": "https://www.youtube.com/watch?v=lmnopqrstuv", "title": "Manual"}],
                    "fetch_titles": False,
                    "resolve_redirects": False,
                })
        self.assertEqual(response.status_code, 200)
        schedule.assert_called_once()

    def test_completed_download_removes_canonical_watch_later_entry(self):
        self.assertTrue(library._remove_downloaded_from_watch_later(
            "https://youtu.be/abcdefghijk?t=5"))
        self.assertIsNone(db.get_watch_later_item(self.item["catalog_id"]))

    def test_startup_reconciles_an_existing_ready_download(self):
        db.create_video("ready-before-start", "https://youtu.be/abcdefghijk", "youtube", "best",
                        self.root / "ready-before-start")
        db.update_video("ready-before-start", status="ready")
        self.assertEqual(library.remove_downloaded_watch_later(), 1)
        self.assertIsNone(db.get_watch_later_item(self.item["catalog_id"]))


if __name__ == "__main__":
    unittest.main()
