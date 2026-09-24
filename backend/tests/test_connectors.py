"""Offline unit tests for the connector layer."""
import os
import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("JOBS_DB_PATH",
                      str(Path(tempfile.mkdtemp()) / "test_jobs.db"))
os.environ.setdefault("MEDIA_ROOT", str(Path(tempfile.mkdtemp()) / "media"))

import yt_dlp  # noqa: E402

from app import config  # noqa: E402
from app.connectors import registry  # noqa: E402
from app.connectors.base import ConnectorError
from app.connectors.ytdlp import _ProgressTracker, _SingleVideoYoutubeDL  # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_registry_resolution(self):
        cases = {
            "https://www.youtube.com/watch?v=abc": "youtube",
            "https://youtu.be/abc": "youtube",
            "https://m.youtube.com/shorts/abc": "youtube",
            "https://music.youtube.com/watch?v=abc": "youtube",
            "https://rumble.com/v1abc-x.html": "rumble",
            "https://vimeo.com/123": "generic",
            "https://example.com/a.mp4": "generic",
        }
        for url, expected in cases.items():
            connector = registry.resolve(url)
            self.assertIsNotNone(connector, url)
            self.assertEqual(connector.id, expected, url)
        for url in ("not a url", "ftp://x/y", "", "https://:80/video",
                    "https://example.com:invalid/x", "https://user:pass@example.com/x",
                    "https://example.com/has space"):
            self.assertIsNone(registry.resolve(url), url)

    def test_describe_order(self):
        described = registry.describe()
        self.assertEqual([c["id"] for c in described],
                         ["youtube", "rumble", "generic"])
        for entry in described:
            self.assertIn("name", entry)
            self.assertIn("domains", entry)


class YdlOptsTest(unittest.TestCase):
    def test_ydl_opts(self):
        dest = Path(tempfile.mkdtemp())
        for connector in registry.CONNECTORS:
            opts = connector.ydl_opts(dest, "1080p", lambda d: None)
            self.assertTrue(opts["outtmpl"].startswith(str(dest)))
            self.assertTrue(opts["noplaylist"])
            self.assertIn("height<=1080", opts["format"])
            self.assertEqual(connector.format_for("best"),
                             "bestvideo+bestaudio/best")
        youtube = registry.get("youtube")
        self.assertIn("avc1", youtube.format_for("720p"))

    def test_thumbnail_conversion_cannot_abort_video_download(self):
        opts = registry.get("generic").ydl_opts(
            Path(tempfile.mkdtemp()), "best", lambda d: None)
        self.assertTrue(opts["writethumbnail"])
        self.assertNotIn("postprocessors", opts)

    def test_source_avif_thumbnail_is_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            video = dest / "video-id.mp4"
            thumbnail = dest / "video-id.avif"
            video.write_bytes(b"video")
            thumbnail.write_bytes(b"thumbnail")
            info = {"id": "video-id", "title": "Example",
                    "filepath": str(video)}
            ydl = MagicMock()
            ydl.__enter__.return_value = ydl
            ydl.extract_info.return_value = info
            with patch("app.connectors.ytdlp._SingleVideoYoutubeDL",
                       return_value=ydl):
                result = registry.get("generic").download(
                    "https://example.com/video", dest)
            self.assertEqual(result.path, video.resolve())
            self.assertEqual(result.thumbnail, thumbnail)

    def test_download_uses_selected_browser_and_profile_without_storing_cookies(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            video = dest / "video-id.mp4"
            video.write_bytes(b"video")
            info = {"id": "video-id", "title": "Example", "filepath": str(video)}
            ydl = MagicMock()
            ydl.__enter__.return_value = ydl
            ydl.extract_info.return_value = info
            with patch("app.connectors.ytdlp._SingleVideoYoutubeDL", return_value=ydl) as constructor, \
                    patch.object(config, "YTDLP_COOKIE_FILE", None):
                registry.get("youtube").download("https://youtu.be/video-id", dest,
                    download_settings={"cookie_browser": "edge", "cookie_browser_profile": "Profile 2"})
            options = constructor.call_args.args[0]
            self.assertEqual(options["cookiesfrombrowser"], ("edge", "Profile 2", None, None))
            self.assertNotIn("cookiefile", options)

    def test_environment_cookie_file_is_validated_and_passed_to_ytdlp(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            cookie_file = dest / "cookies.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            video = dest / "video-id.mp4"
            video.write_bytes(b"video")
            ydl = MagicMock()
            ydl.__enter__.return_value = ydl
            ydl.extract_info.return_value = {"id": "video-id", "title": "Example", "filepath": str(video)}
            with patch("app.connectors.ytdlp._SingleVideoYoutubeDL", return_value=ydl) as constructor, \
                    patch.object(config, "YTDLP_COOKIE_FILE", cookie_file):
                registry.get("youtube").download("https://youtu.be/video-id", dest)
            cookie_input = constructor.call_args.args[0]["cookiefile"]
            self.assertIsInstance(cookie_input, io.StringIO)
            self.assertTrue(cookie_input.closed)
            self.assertEqual(cookie_file.read_text(encoding="utf-8"), "# Netscape HTTP Cookie File\n")
            with patch.object(config, "YTDLP_COOKIE_FILE", dest / "missing.txt"):
                with self.assertRaisesRegex(ConnectorError, "does not exist"):
                    registry.get("youtube").download("https://youtu.be/video-id", dest)

    def test_playlists_are_rejected_before_download(self):
        with _SingleVideoYoutubeDL({"quiet": True}) as ydl:
            with self.assertRaisesRegex(ConnectorError, "single video link"):
                ydl.process_ie_result({"_type": "playlist", "entries": []})

    def test_missing_requested_filepath_uses_merged_output(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder)
            merged = dest / "merged.mp4"
            merged.write_bytes(b"fixture")
            ydl = Mock()
            ydl.prepare_filename.return_value = str(dest / "missing.webm")
            path = registry.get("youtube")._final_path(
                ydl, {"requested_downloads": [{}]}, dest)
            self.assertEqual(path, merged.resolve())


class ProgressTrackerTest(unittest.TestCase):
    def test_two_files_monotonic(self):
        reported = []
        tracker = _ProgressTracker(
            lambda pct, stage: reported.append((pct, stage)), None)
        info = {"requested_formats": [{"format_id": "v"},
                                      {"format_id": "a"}]}

        tracker({"status": "downloading", "downloaded_bytes": 50,
                 "total_bytes": 100, "info_dict": info})
        tracker({"status": "downloading", "downloaded_bytes": 100,
                 "total_bytes": 100, "info_dict": info})
        tracker({"status": "finished", "info_dict": info})
        after_first_finished = reported[-1][0]
        tracker({"status": "downloading", "downloaded_bytes": 25,
                 "total_bytes": 100, "info_dict": info})
        tracker({"status": "downloading", "downloaded_bytes": 100,
                 "total_bytes": 100, "info_dict": info})
        tracker({"status": "finished", "info_dict": info})
        after_second_finished = reported[-1][0]
        tracker({"status": "started", "postprocessor": "Merger"})

        percents = [p for p, _ in reported]
        self.assertEqual(percents, sorted(percents))
        self.assertAlmostEqual(after_first_finished, 45.0, places=2)
        self.assertAlmostEqual(after_second_finished, 90.0, places=2)
        self.assertEqual(reported[-1], (90, "merging"))

    def test_thumbnail_postprocessing_does_not_finish_download_progress(self):
        reported = []
        tracker = _ProgressTracker(lambda pct, stage: reported.append((pct, stage)), None)
        tracker({"postprocessor": "ThumbnailsConvertor", "status": "started"})
        self.assertEqual(reported, [])

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        tracker = _ProgressTracker(None, cancel)
        with self.assertRaises(yt_dlp.utils.DownloadCancelled):
            tracker({"status": "downloading", "info_dict": {}})


if __name__ == "__main__":
    unittest.main()
