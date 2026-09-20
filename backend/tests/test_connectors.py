"""Offline unit tests for the connector layer."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("JOBS_DB_PATH",
                      str(Path(tempfile.mkdtemp()) / "test_jobs.db"))
os.environ.setdefault("MEDIA_ROOT", str(Path(tempfile.mkdtemp()) / "media"))

import yt_dlp  # noqa: E402

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
