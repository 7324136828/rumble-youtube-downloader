"""Codec and seeking regressions using generated local media only."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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


class NativePlaybackTest(unittest.TestCase):
    def test_webm_codec_candidates_include_av1_and_both_audio_codecs(self):
        for codec in ("vp8", "vp9", "av1"):
            for audio in ("opus", "vorbis"):
                streams = [{"codec_type": "video", "codec_name": codec},
                           {"codec_type": "audio", "codec_name": audio}]
                with self.subTest(codec=codec, audio=audio), \
                        patch.object(media, "probe_streams", return_value=streams):
                    self.assertTrue(media.is_browser_compatible(Path("video.webm")))

    def test_unsupported_webm_track_still_reports_incompatible(self):
        streams = [{"codec_type": "video", "codec_name": "vp9"},
                   {"codec_type": "audio", "codec_name": "unknown"}]
        with patch.object(media, "probe_streams", return_value=streams):
            self.assertFalse(media.is_browser_compatible(Path("video.webm")))

    def test_legacy_playback_does_not_start_conversion_when_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "outputs" / "video.mkv"
            original.parent.mkdir()
            original.write_bytes(b"original media")
            with patch.object(media.db, "get_download_settings", return_value={
                    "convert_for_browser": False}), \
                    patch.object(media.threading, "Thread") as thread:
                self.assertEqual(media.ensure_streamable(original), original)
                self.assertEqual(media.video_state(Path(folder), original), "ready")
                thread.assert_not_called()
                self.assertFalse(media.stream_path_for(original).exists())


class ThumbnailPreparationTest(unittest.TestCase):
    def test_timeouts_are_bounded_and_do_not_leave_partial_thumbnails(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "thumbnail.jpg"
            target.write_bytes(b"partial")
            with patch.object(media.subprocess, "run", side_effect=
                              subprocess.TimeoutExpired("ffmpeg", 20)) as run:
                self.assertFalse(media.make_thumbnail(Path(folder) / "video.mp4", target))
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["timeout"], 20)
                self.assertEqual(call.kwargs["creationflags"],
                                 getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertFalse(target.exists())

    def test_missing_ffmpeg_does_not_fail_thumbnail_preparation(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(media.subprocess, "run", side_effect=FileNotFoundError):
            self.assertFalse(media.make_thumbnail(Path(folder) / "video.mp4",
                                                   Path(folder) / "thumbnail.jpg"))


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

    def test_audio_track_is_extracted_as_mp3(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.m4a"
            target = Path(folder) / "audio.mp3"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
                 "-c:a", "aac", str(source)], check=True)
            self.assertTrue(media.convert_to_mp3(source, target))
            self.assertTrue(target.is_file())
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "stream=codec_name", "-of", "default=nw=1:nk=1", str(target)],
                capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "mp3")


if __name__ == "__main__":
    unittest.main()
