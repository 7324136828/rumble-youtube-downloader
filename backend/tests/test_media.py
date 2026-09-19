"""Codec and seeking regressions using generated local media only."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JOBS_DB_PATH", str(Path(tempfile.mkdtemp()) / "test_jobs.db"))

from app.services import media  # noqa: E402


class ByteRangeTest(unittest.TestCase):
    def test_ranges(self):
        cases = [
            (None, 100, (0, 99, 200)),
            ("bytes=0-0", 100, (0, 0, 206)),
            ("bytes=90-200", 100, (90, 99, 206)),
            ("bytes=-10", 100, (90, 99, 206)),
            ("bytes=-200", 100, (0, 99, 206)),
            ("bytes=100-", 100, (0, -1, 416)),
            ("bytes=20-10", 100, (0, -1, 416)),
            ("bytes=-0", 100, (0, -1, 416)),
            ("bytes=0-0", 0, (0, -1, 416)),
            ("bytes=0-1,8-9", 100, (0, 99, 200)),
        ]
        for header, size, expected in cases:
            with self.subTest(header=header, size=size):
                self.assertEqual(media.parse_range(header, size), expected)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg and ffprobe required")
class CodecPreparationTest(unittest.TestCase):
    def test_mp4_with_unsupported_codec_is_transcoded(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "mpeg4.mp4"
            target = Path(folder) / "browser.mp4"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc=duration=0.3:size=64x64:rate=10",
                 "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(source)],
                check=True)
            self.assertFalse(media.is_browser_compatible(source))
            self.assertTrue(media.convert_to_mp4(source, target))
            self.assertTrue(media.is_browser_compatible(target))
            self.assertEqual(media.probe_streams(target)[0]["codec_name"], "h264")

    def test_cancelled_transcode_stops_process(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            media._run_conversion([sys.executable, "-c", "import time; time.sleep(30)"],
                                  cancelled)


if __name__ == "__main__":
    unittest.main()
