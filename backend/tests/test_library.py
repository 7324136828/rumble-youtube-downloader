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
from unittest.mock import patch

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
FIXTURE_WEBM = {codec: FIXTURE_DIR / f"{codec}.webm" for codec in ("vp9", "av1")}


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
        if "webm" in url:
            source = FIXTURE_WEBM["av1" if "av1" in url else "vp9"]
        else:
            source = FIXTURE_MKV if "mkv" in url else FIXTURE_MP4
        target = dest_dir / f"fake{source.suffix}"
        shutil.copy(source, target)
        if on_progress:
            on_progress(50, "downloading")
        thumbnail = None
        if "thumbnail" in url:
            thumbnail = dest_dir / "source.jpg"
            thumbnail.write_bytes(b"source thumbnail")
        return DownloadResult(
            path=target,
            info=VideoInfo(title="Fake video", uploader="Tester",
                           duration=1.0, width=64, height=64),
            thumbnail=thumbnail)


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
        for codec, fixture in FIXTURE_WEBM.items():
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc=duration=0.3:size=64x64:rate=10",
                 "-c:v", "libaom-av1" if codec == "av1" else "libvpx-vp9",
                 "-cpu-used", "8", "-threads", "1", "-pix_fmt", "yuv420p",
                 str(fixture)], check=True)
        db.init_db()
        registry.CONNECTORS.insert(0, FAKE)

    @classmethod
    def tearDownClass(cls):
        registry.CONNECTORS.remove(FAKE)

    def setUp(self):
        self.settings = {"convert_for_browser": True, "generate_thumbnails": True}
        settings_patch = patch.object(db, "get_download_settings",
                                      return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

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
        self.assertIsNone(result["playback_warning"])

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
        with patch.object(db, "update_video", wraps=db.update_video) as update:
            row = library.start_download("https://fake.test/mkv")
            result = library.wait_for(row["id"], 30)
        self.assertEqual(result["status"], "ready")
        stages = [call.kwargs.get("stage") for call in update.call_args_list]
        self.assertLess(stages.index("checking"), stages.index("converting"))
        self.assertLess(stages.index("converting"), stages.index("thumbnail"))
        file_path = Path(result["file_path"])
        self.assertEqual(file_path.suffix, ".mp4")
        self.assertFalse((file_path.parent / "fake.mkv").exists())
        self.assertIsNone(result["playback_warning"])
        library.cancel_and_delete(row["id"])

    def test_native_webm_is_kept_without_conversion(self):
        self.settings.update(convert_for_browser=False, generate_thumbnails=False)
        for codec, fixture in FIXTURE_WEBM.items():
            with self.subTest(codec=codec), \
                    patch.object(library.media, "convert_to_mp4") as convert:
                row = library.start_download(f"https://fake.test/{codec}.webm")
                result = library.wait_for(row["id"], 15)
                convert.assert_not_called()
                self.assertEqual(result["status"], "ready", result["error_message"])
                self.assertIsNone(result["playback_warning"])
                self.assertEqual(Path(result["file_path"]).read_bytes(), fixture.read_bytes())
                self.assertEqual(library.video_payload(result)["file_name"], "fake.webm")
                self.assertFalse(list(Path(result["media_dir"]).glob("*.mp4")))
                library.cancel_and_delete(row["id"])

    def test_explicit_mp4_option_converts_playable_webm(self):
        self.settings["generate_thumbnails"] = False
        with patch.object(library.media, "convert_to_mp4",
                          wraps=library.media.convert_to_mp4) as convert:
            row = library.start_download("https://fake.test/vp9.webm")
            result = library.wait_for(row["id"], 30)
        convert.assert_called_once()
        self.assertEqual(result["status"], "ready", result["error_message"])
        path = Path(result["file_path"])
        self.assertEqual(path.suffix, ".mp4")
        self.assertTrue(library.media.is_browser_compatible(path))
        self.assertFalse((path.parent / "fake.webm").exists())
        self.assertIsNone(result["playback_warning"])
        library.cancel_and_delete(row["id"])

    def test_explicit_mp4_option_keeps_already_compatible_mp4(self):
        with patch.object(library.media, "convert_to_mp4") as convert:
            row = library.start_download("https://fake.test/ok")
            result = library.wait_for(row["id"], 15)
        convert.assert_not_called()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(Path(result["file_path"]).read_bytes(), FIXTURE_MP4.read_bytes())
        library.cancel_and_delete(row["id"])

    def test_disabling_conversion_still_checks_downloaded_media(self):
        self.settings["convert_for_browser"] = False
        with patch.object(library.media, "is_browser_compatible", side_effect=
                          ValueError("The source did not contain a video track.")):
            row = library.start_download("https://fake.test/ok")
            result = library.wait_for(row["id"], 15)
        self.assertEqual(result["status"], "failed")
        self.assertIn("did not contain a video track", result["error_message"])
        library.cancel_and_delete(row["id"])

    def test_conversion_can_be_disabled_and_original_remains_downloadable(self):
        self.settings["convert_for_browser"] = False
        with patch.object(library.media, "convert_to_mp4") as convert:
            row = library.start_download("https://fake.test/mkv")
            result = library.wait_for(row["id"], 15)
        convert.assert_not_called()
        self.assertEqual(result["status"], "ready")
        source = Path(result["file_path"])
        self.assertEqual(source.suffix, ".mkv")
        self.assertEqual(source.read_bytes(), FIXTURE_MKV.read_bytes())
        self.assertIn("Automatic conversion is off", result["playback_warning"])
        self.assertEqual(library.video_payload(result)["file_name"], "fake.mkv")
        library.cancel_and_delete(row["id"])

    def test_optional_thumbnails_preserve_the_source_thumbnail(self):
        self.settings["generate_thumbnails"] = False
        for video_url, has_source in (("ok", False), ("thumbnail", True)):
            with self.subTest(video_url=video_url), \
                    patch.object(library.media, "make_thumbnail") as make_thumbnail:
                row = library.start_download(f"https://fake.test/{video_url}")
                result = library.wait_for(row["id"], 15)
                make_thumbnail.assert_not_called()
                self.assertEqual(result["status"], "ready")
                self.assertEqual(bool(result["thumbnail_path"]), has_source)
                if has_source:
                    self.assertEqual(Path(result["thumbnail_path"]).read_bytes(),
                                     b"source thumbnail")
                library.cancel_and_delete(row["id"])

    def test_settings_are_snapshotted_when_queued(self):
        self.settings.update(convert_for_browser=False, generate_thumbnails=False)
        blocked_slot = threading.Semaphore(0)
        with patch.object(library, "_SLOTS", blocked_slot), \
                patch.object(library.media, "convert_to_mp4") as convert, \
                patch.object(library.media, "make_thumbnail") as make_thumbnail:
            row = library.start_download("https://fake.test/mkv")
            self.assertEqual(row["status"], "queued")
            self.settings.update(convert_for_browser=True, generate_thumbnails=True)
            blocked_slot.release()
            result = library.wait_for(row["id"], 15)
        self.assertEqual(result["status"], "ready")
        convert.assert_not_called()
        make_thumbnail.assert_not_called()
        self.assertIn("Automatic conversion is off", result["playback_warning"])
        library.cancel_and_delete(row["id"])

    def test_conversion_failure_preserves_original(self):
        with patch.object(library.media, "convert_to_mp4", return_value=False):
            row = library.start_download("https://fake.test/mkv")
            result = library.wait_for(row["id"], 15)
        self.assertEqual(result["status"], "failed")
        self.assertEqual((Path(result["media_dir"]) / "fake.mkv").read_bytes(),
                         FIXTURE_MKV.read_bytes())
        library.cancel_and_delete(row["id"])

    def test_thumbnail_failure_does_not_fail_completed_video(self):
        with patch.object(library.media, "make_thumbnail", return_value=False):
            row = library.start_download("https://fake.test/ok")
            result = library.wait_for(row["id"], 15)
        self.assertEqual(result["status"], "ready")
        self.assertIsNone(result["thumbnail_path"])
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
