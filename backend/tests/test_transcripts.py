"""Transcript source selection, text normalization, and worker lifecycle."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config
from app.services import db, library, transcripts


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.settings = patch.object(config, "JOBS_DB_PATH", self.root / "jobs.db")
        self.settings.start()
        db.init_db()
        self.media = self.root / "media"
        self.media.mkdir()
        self.source = self.media / "video.mp4"
        self.source.write_bytes(b"fixture")
        db.create_video("sample", "https://example.test/video", "generic", "best", str(self.media))
        db.update_video("sample", status="ready", file_path=str(self.source))

    def tearDown(self):
        transcripts.shutdown()
        self.settings.stop()
        self.directory.cleanup()

    def wait_for_transcript(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with transcripts._LOCK:
                active = "sample" in transcripts._JOBS
            if not active:
                return db.get_video_transcript("sample")
            time.sleep(0.01)
        self.fail("Transcript worker did not stop")

    def test_manual_source_captions_precede_automatic_captions(self):
        info = {"language": "es", "subtitles": {"fr": [{}], "es": [{}], "live_chat": [{}]},
                "automatic_captions": {"en": [{}]}}
        self.assertEqual(transcripts.select_subtitle_language(info), "es")
        info["subtitles"] = {"live_chat": [{}]}
        self.assertEqual(transcripts.select_subtitle_language(info), "en")
        self.assertIsNone(transcripts.select_subtitle_language({"subtitles": {"en": []}}))

    def test_webvtt_removes_timing_tags_and_rolling_caption_duplicates(self):
        path = self.root / "captions.vtt"
        path.write_text("WEBVTT\nKind: captions\n\n00:00:00.000 --> 00:00:03.000\n"
                        "<c>Hello &amp; welcome</c>\n\n00:00:02.000 --> 00:00:04.000\n"
                        "Hello &amp; welcome to the video.\n\n"
                        "00:00:04.000 --> 00:00:06.000\nThank you.\n", encoding="utf-8")
        self.assertEqual(transcripts.subtitle_text(path, rolling=True), "Hello & welcome to the video. Thank you.")

    def test_independent_cues_preserve_repeated_speech_and_ttml_line_breaks(self):
        path = self.root / "repeats.vtt"
        path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nNo.\n\n"
                        "00:00:01.000 --> 00:00:02.000\nNo.\n\n"
                        "00:00:02.000 --> 00:00:03.000\nThank you.\n\n"
                        "00:00:03.000 --> 00:00:04.000\nThank you.\n")
        for rolling in (False, True):
            self.assertEqual(transcripts.subtitle_text(path, rolling=rolling), "No. No. Thank you. Thank you.")
        xml = self.root / "captions.ttml"
        xml.write_text('<tt xmlns="http://www.w3.org/ns/ttml"><body><p>Hello<br/>world.</p></body></tt>')
        self.assertEqual(transcripts.subtitle_text(xml), "Hello world.")
        xml.write_text('<tt><body><p>2 &lt; 3 &gt; 1</p></body></tt>')
        self.assertEqual(transcripts.subtitle_text(xml), "2 < 3 > 1")

    def test_srt_and_json3_yield_plain_unicode_text(self):
        srt = self.root / "captions.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nCafé <i>today</i>.\n\n"
                       "2\n00:00:03,000 --> 00:00:04,000\nAnother line.\n", encoding="utf-8-sig")
        self.assertEqual(transcripts.subtitle_text(srt), "Café today. Another line.")
        json3 = self.root / "captions.json3"
        json3.write_text(json.dumps({"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "world."}]},
                                                 {"segs": [{"utf8": "Next sentence."}]}]}))
        self.assertEqual(transcripts.subtitle_text(json3), "Hello world. Next sentence.")
        json3.write_text(json.dumps({"events": [{"segs": [{"utf8": "Use <name> here"}]}]}))
        self.assertEqual(transcripts.subtitle_text(json3), "Use <name> here")

    def test_source_transcript_is_cached_and_whisper_is_not_called(self):
        with patch.object(transcripts, "_fetch_subtitles", return_value=("Source transcript", "en")) as captions, \
                patch.object(transcripts, "_generate_whisper") as whisper:
            self.assertIn(transcripts.start_transcript("sample")["status"], {"queued", "fetching", "completed"})
            self.assertEqual(self.wait_for_transcript()["source"], "subtitles")
            payload = transcripts.transcript_payload("sample", include_text=True)
            self.assertEqual(payload["text"], "Source transcript\n")
            self.assertEqual(payload["download_url"], "/api/media/sample/transcript/download")
            self.assertEqual(transcripts.start_transcript("sample")["status"], "completed")
            captions.assert_called_once()
            whisper.assert_not_called()
        self.assertFalse((self.media / ".transcript-work").exists())

    def test_failed_source_lookup_falls_back_to_whisper_and_preserves_language(self):
        with patch.object(transcripts, "_fetch_subtitles", side_effect=RuntimeError("source unavailable")), \
                patch.object(transcripts, "_generate_whisper", return_value=("Spoken words", "de")):
            transcripts.start_transcript("sample")
            row = self.wait_for_transcript()
        self.assertEqual((row["status"], row["source"], row["language"]), ("completed", "whisper", "de"))

    def test_source_subtitle_download_selects_one_track_and_uses_cookie_copy(self):
        work = self.media / ".transcript-work"
        work.mkdir()
        cookies = self.root / "cookies.txt"
        original = "# Netscape HTTP Cookie File\nexample.test\tFALSE\t/\tTRUE\t0\tsession\tprivate\n"
        cookies.write_text(original)
        commands = []
        def child(video_id, cancel, command, timeout):
            commands.append(command)
            if "--dump-single-json" in command:
                return json.dumps({"language": "en", "subtitles": {"en": [{"ext": "vtt"}]}})
            (work / "subtitle.en.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nCaption text.\n")
            return ""
        with patch.object(db, "get_download_settings", return_value={"cookie_file": str(cookies)}), \
                patch.object(transcripts, "_command", side_effect=child):
            self.assertEqual(transcripts._fetch_subtitles("sample", db.get_video("sample"), work,
                                                        threading.Event()), ("Caption text.", "en"))
        self.assertEqual(cookies.read_text(), original)
        for command in commands:
            self.assertEqual(command[command.index("--cookies") + 1], str(work / "cookies.txt"))
            self.assertEqual(command[-2:], ["--", "https://example.test/video"])
        self.assertEqual(commands[1][commands[1].index("--sub-langs") + 1], "^en$")
        self.assertIn("--skip-download", commands[1])
        self.assertIn("--write-subs", commands[1])

    def test_uploaded_media_skips_source_caption_requests(self):
        video = {**db.get_video("sample"), "connector": "upload"}
        with patch.object(transcripts, "_command") as child:
            self.assertIsNone(transcripts._fetch_subtitles("sample", video, self.media, threading.Event()))
            child.assert_not_called()

    def test_inference_failure_is_terminal_and_retryable(self):
        with patch.object(transcripts, "_fetch_subtitles", return_value=None), \
                patch.object(transcripts, "_generate_whisper", side_effect=RuntimeError("no speech")):
            transcripts.start_transcript("sample")
            row = self.wait_for_transcript()
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error_message"], "no speech")
            self.assertIsNotNone(row["completed_at"])
        with patch.object(transcripts, "_fetch_subtitles", return_value=("Retry succeeded", "en")):
            transcripts.start_transcript("sample")
            self.assertEqual(self.wait_for_transcript()["status"], "completed")

    def test_duplicate_requests_share_worker_and_delete_cancels_before_purge(self):
        started = threading.Event()
        def waiting(video_id, video, work, cancel):
            started.set()
            cancel.wait(5)
            transcripts._check_cancel(video_id, cancel)
        with patch.object(transcripts, "_fetch_subtitles", side_effect=waiting) as captions:
            transcripts.start_transcript("sample")
            self.assertTrue(started.wait(3))
            transcripts.start_transcript("sample")
            captions.assert_called_once()
            library.cancel_and_delete("sample")
        self.assertIsNone(db.get_video("sample"))
        self.assertIsNone(db.get_video_transcript("sample"))
        self.assertFalse(self.media.exists())
        with transcripts._LOCK:
            self.assertNotIn("sample", transcripts._JOBS)

    def test_transcript_read_rejects_path_outside_video_directory(self):
        external = self.root / "private.txt"
        external.write_text("private")
        db.upsert_video_transcript("sample")
        db.update_video_transcript("sample", status="completed", text_path=str(external))
        payload = transcripts.transcript_payload("sample", include_text=True)
        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["text"])
        with self.assertRaises(RuntimeError):
            transcripts.transcript_file("sample")

    def test_not_ready_media_cannot_queue_and_deleting_media_cannot_restart(self):
        db.update_video("sample", status="downloading")
        with self.assertRaises(RuntimeError):
            transcripts.start_transcript("sample")
        db.update_video("sample", status="ready")
        with patch.object(library, "_DELETING", {"sample"}):
            with self.assertRaises(LookupError):
                transcripts.start_transcript("sample")


if __name__ == "__main__":
    unittest.main()
