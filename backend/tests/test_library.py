"""Library service tests using a fake connector (no network)."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("JOBS_DB_PATH",
                      str(Path(tempfile.mkdtemp()) / "test_jobs.db"))
os.environ.setdefault("MEDIA_ROOT", str(Path(tempfile.mkdtemp()) / "media"))

from app.connectors import (Connector, ConnectorCancelled,  # noqa: E402
                            ConnectorError, DownloadResult, VideoInfo,
                            registry)
from app.services import db, library  # noqa: E402

FIXTURE_DIR = Path(tempfile.mkdtemp())
FIXTURE_MP4 = FIXTURE_DIR / "fixture.mp4"
FIXTURE_MKV = FIXTURE_DIR / "fixture.mkv"


class FakeConnector(Connector):
    id = "fake"
    name = "Fake"
    domains = ()

    def matches(self, url: str) -> bool:
        return url.startswith("https://fake.test/")

    def download(self, url, dest_dir, quality="best", on_progress=None,
                 cancel=None):
        if "fail" in url:
            raise ConnectorError("boom")
        if "slow" in url:
            deadline = time.time() + 10
            while not (cancel and cancel.is_set()):
                if time.time() > deadline:
                    break
                if on_progress:
                    on_progress(10, "downloading")
                time.sleep(0.05)
            raise ConnectorCancelled()
        source = FIXTURE_MKV if "mkv" in url else FIXTURE_MP4
        target = dest_dir / f"fake{source.suffix}"
        shutil.copy(source, target)
        if on_progress:
            on_progress(50, "downloading")
        return DownloadResult(
            path=target,
            info=VideoInfo(title="Fake video", uploader="Tester",
                           duration=1.0, width=64, height=64))


FAKE = FakeConnector()


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class LibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for fixture in (FIXTURE_MP4, FIXTURE_MKV):
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i",
                 "testsrc=duration=1:size=64x64:rate=10",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fixture)],
                check=True)
        db.init_db()
        registry.CONNECTORS.insert(0, FAKE)

    @classmethod
    def tearDownClass(cls):
        registry.CONNECTORS.remove(FAKE)

    def test_download_lifecycle(self):
        row = library.start_download("https://fake.test/ok")
        result = library.wait_for(row["id"], 15)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["progress"], 100)
        file_path = Path(result["file_path"])
        self.assertTrue(file_path.is_file())
        self.assertEqual(file_path.suffix, ".mp4")
        self.assertTrue(Path(result["thumbnail_path"]).is_file())
        self.assertEqual(result["title"], "Fake video")
        self.assertEqual(result["uploader"], "Tester")

        payload = library.video_payload(result)
        self.assertEqual(payload["stream_url"],
                         f"/api/media/{row['id']}/stream")
        self.assertEqual(payload["thumbnail_url"],
                         f"/api/media/{row['id']}/thumbnail")
        self.assertEqual(payload["download_url"],
                         f"/api/media/{row['id']}/download")
        self.assertEqual(payload["file_name"], file_path.name)
        for key in ("media_dir", "file_path", "thumbnail_path"):
            self.assertNotIn(key, payload)
        library.cancel_and_delete(row["id"])

    def test_mkv_is_remuxed(self):
        row = library.start_download("https://fake.test/mkv")
        result = library.wait_for(row["id"], 30)
        self.assertEqual(result["status"], "ready")
        file_path = Path(result["file_path"])
        self.assertEqual(file_path.suffix, ".mp4")
        self.assertFalse(file_path.with_suffix(".mkv").exists())
        library.cancel_and_delete(row["id"])

    def test_failed_download(self):
        row = library.start_download("https://fake.test/fail")
        result = library.wait_for(row["id"], 15)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_message"], "boom")

    def test_delete_removes_files_and_row(self):
        row = library.start_download("https://fake.test/ok")
        result = library.wait_for(row["id"], 15)
        media_dir = Path(result["media_dir"])
        library.cancel_and_delete(row["id"])
        self.assertIsNone(db.get_video(row["id"]))
        self.assertFalse(media_dir.exists())

    def test_cancel_during_download(self):
        row = library.start_download("https://fake.test/slow")
        deadline = time.time() + 5
        status = None
        while time.time() < deadline:
            status = db.get_video(row["id"])["status"]
            if status == "downloading":
                break
            time.sleep(0.05)
        self.assertEqual(status, "downloading")
        library.cancel_and_delete(row["id"])
        self.assertIsNone(db.get_video(row["id"]))
        self.assertFalse(Path(row["media_dir"]).exists())

    def test_cancel_queued_job_never_starts_connector(self):
        from unittest.mock import patch
        unavailable_slot = threading.Semaphore(0)
        with patch.object(library, "_SLOTS", unavailable_slot), \
                patch.object(FAKE, "download") as download:
            row = library.start_download("https://fake.test/queued")
            library.cancel_and_delete(row["id"])
            self.assertIsNone(db.get_video(row["id"]))
            self.assertFalse(Path(row["media_dir"]).exists())
            download.assert_not_called()
            with library._LOCK:
                self.assertNotIn(row["id"], library._ACTIVE)

    def test_unsupported_url_raises(self):
        with self.assertRaises(ValueError):
            library.start_download("ftp://nope")


if __name__ == "__main__":
    unittest.main()
