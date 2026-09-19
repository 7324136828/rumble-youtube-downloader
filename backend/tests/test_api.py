"""API tests for the media library endpoints (fake connector, no network)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("JOBS_DB_PATH",
                      str(Path(tempfile.mkdtemp()) / "test_jobs.db"))
os.environ.setdefault("MEDIA_ROOT", str(Path(tempfile.mkdtemp()) / "media"))

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.connectors import (Connector, ConnectorCancelled,  # noqa: E402
                            ConnectorError, DownloadResult, VideoInfo,
                            registry)
from app.main import app  # noqa: E402
from app.services import db, library  # noqa: E402

FIXTURE_DIR = Path(tempfile.mkdtemp())
FIXTURE_MP4 = FIXTURE_DIR / "fixture.mp4"


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
        target = dest_dir / "fake.mp4"
        shutil.copy(FIXTURE_MP4, target)
        if on_progress:
            on_progress(50, "downloading")
        return DownloadResult(
            path=target,
            info=VideoInfo(title="Fake video", uploader="Tester",
                           duration=1.0, width=64, height=64))


FAKE = FakeConnector()


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not available")
class MediaApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(FIXTURE_MP4)],
            check=True)
        cls.client_ctx = TestClient(app)
        cls.client = cls.client_ctx.__enter__()
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)

    def _register_fake(self):
        if FAKE not in registry.CONNECTORS:
            registry.CONNECTORS.insert(0, FAKE)
        self.addCleanup(lambda: registry.CONNECTORS.remove(FAKE))

    def test_connectors_endpoint(self):
        resp = self.client.get("/api/connectors")
        self.assertEqual(resp.status_code, 200)
        described = [c for c in resp.json() if c["id"] != "fake"]
        self.assertEqual([c["id"] for c in described],
                         ["youtube", "rumble", "generic"])

    def test_resolve_endpoint(self):
        resp = self.client.post("/api/resolve", json={
            "urls": ["https://youtu.be/x", "https://rumble.com/v1.html",
                     "https://example.com/a.mp4", "nope"]})
        self.assertEqual(resp.status_code, 200)
        results = resp.json()
        self.assertEqual(len(results), 4)
        ids = [r["connector"]["id"] if r["connector"] else None
               for r in results]
        self.assertEqual(ids, ["youtube", "rumble", "generic", None])

    def test_unsupported_url_rejected(self):
        resp = self.client.post("/api/media", json={"urls": ["nope"]})
        self.assertEqual(resp.status_code, 400)

    def test_full_flow(self):
        self._register_fake()
        resp = self.client.post("/api/media", json={
            "urls": ["https://fake.test/ok"], "quality": "720p"})
        self.assertEqual(resp.status_code, 200)
        items = resp.json()
        self.assertEqual(len(items), 1)
        self.assertIn(items[0]["status"], ("queued", "downloading"))
        video_id = items[0]["id"]

        result = library.wait_for(video_id, 15)
        self.assertEqual(result["status"], "ready")

        resp = self.client.get(f"/api/media/{video_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")

        resp = self.client.get(f"/api/media/{video_id}/stream",
                               headers={"Range": "bytes=0-0"})
        self.assertEqual(resp.status_code, 206)
        self.assertTrue(resp.headers["Content-Range"]
                        .startswith("bytes 0-0/"))
        self.assertEqual(resp.headers["Content-Length"], "1")

        size = Path(result["file_path"]).stat().st_size
        resp = self.client.get(f"/api/media/{video_id}/stream")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("content-range", resp.headers)
        self.assertEqual(len(resp.content), size)

        resp = self.client.get(f"/api/media/{video_id}/stream",
                               headers={"Range": f"bytes={size}-"})
        self.assertEqual(resp.status_code, 416)
        self.assertEqual(resp.headers["content-range"], f"bytes */{size}")

        resp = self.client.get(f"/api/media/{video_id}/stream",
                               headers={"Range": f"bytes=0-{size + 100}"})
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(len(resp.content), size)

        resp = self.client.get(f"/api/media/{video_id}/stream",
                               headers={"Range": "bytes=-10"})
        self.assertEqual(resp.status_code, 206)
        self.assertEqual(len(resp.content), 10)

        resp = self.client.get(f"/api/media/{video_id}/thumbnail")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("image/"))

        resp = self.client.get(f"/api/media/{video_id}/download")
        self.assertEqual(resp.status_code, 200)
        from urllib.parse import unquote
        self.assertIn("Fake video",
                      unquote(resp.headers["content-disposition"]))

        resp = self.client.get("/api/media", params={"status": "ready"})
        self.assertTrue(any(v["id"] == video_id for v in resp.json()))

        resp = self.client.delete(f"/api/media/{video_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["deleted"])
        self.assertEqual(self.client.get(f"/api/media/{video_id}")
                         .status_code, 404)

    def test_stream_not_ready_conflict(self):
        row = db.create_video("queued-vid", "https://fake.test/ok", "fake",
                              "best", tempfile.mkdtemp())
        resp = self.client.get(f"/api/media/{row['id']}/stream")
        self.assertEqual(resp.status_code, 409)

    def test_convert_unavailable(self):
        if config.DOWNLOADER_SCRIPT.is_file():
            self.skipTest("legacy script present; convert is enabled")
        resp = self.client.post("/api/convert",
                                json={"urls": ["https://youtu.be/x"]})
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
