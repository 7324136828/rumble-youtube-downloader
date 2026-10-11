"""Native library transcript routes, persistence, and deletion lifecycle."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import db, library, transcripts


class TranscriptFixture(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        self.enterContext(patch.object(config, "MEDIA_ROOT", self.folder / "library"))
        db.init_db()
        self.video_id = "saved-media"
        self.media_dir = self.folder / "library" / self.video_id
        self.media_dir.mkdir(parents=True)
        self.source = self.media_dir / "saved.webm"
        self.source.write_bytes(b"saved media")
        db.create_video(self.video_id, "https://example.com/saved", "generic", "best", self.media_dir)
        db.complete_video(self.video_id, "2026-10-11T00:00:00+00:00", progress=100,
                          title="Saved media", file_path=str(self.source))


class TranscriptStoreTest(TranscriptFixture):
    def test_retry_resets_failed_attempt_and_keeps_video(self):
        with patch.object(db, "_now", return_value="first-attempt"):
            queued = db.upsert_video_transcript(self.video_id)
        self.assertEqual(queued["status"], "queued")
        failed = db.update_video_transcript(
            self.video_id, status="failed", text_path="old.txt", source="whisper",
            language="en", error_message="Recognition failed", completed_at="old-completion")
        self.assertEqual(failed["error_message"], "Recognition failed")

        with patch.object(db, "_now", return_value="retry-attempt"):
            retry = db.upsert_video_transcript(self.video_id)

        self.assertEqual(retry["status"], "queued")
        self.assertEqual(retry["created_at"], "retry-attempt")
        for name in ("text_path", "source", "language", "error_message", "completed_at"):
            self.assertIsNone(retry[name])
        self.assertEqual(db.get_video(self.video_id)["status"], "ready")
        self.assertTrue(self.source.is_file())

    def test_missing_media_cannot_gain_an_orphan_transcript(self):
        with self.assertRaisesRegex(LookupError, "Video not found"):
            db.upsert_video_transcript("missing")
        self.assertIsNone(db.get_video_transcript("missing"))
        self.assertIsNone(db.update_video_transcript("missing", status="completed"))

    def test_restart_fails_only_running_transcripts(self):
        for status in ("idle", "queued", "fetching", "transcribing", "completed", "failed"):
            db.create_video(status, f"https://example.com/{status}", "generic", "best", self.media_dir)
            db.upsert_video_transcript(status, status=status)
            if status in ("completed", "failed"):
                db.update_video_transcript(status, completed_at="previous-completion")

        db.fail_stale_transcripts()

        for original in ("idle", "queued", "fetching", "transcribing", "completed", "failed"):
            with self.subTest(original=original):
                row = db.get_video_transcript(original)
                if original in ("queued", "fetching", "transcribing"):
                    self.assertEqual(row["status"], "failed")
                    self.assertIn("restarted", row["error_message"])
                    self.assertIsNotNone(row["completed_at"])
                else:
                    self.assertEqual(row["status"], original)
                    self.assertIsNone(row["error_message"])

    def test_parent_deletion_cascades_transcript_row(self):
        db.upsert_video_transcript(self.video_id)
        db.delete_video(self.video_id)
        self.assertIsNone(db.get_video_transcript(self.video_id))

    def test_library_delete_cancels_transcript_before_file_purge(self):
        db.upsert_video_transcript(self.video_id)

        def cancel(video_id):
            self.assertEqual(video_id, self.video_id)
            self.assertTrue(self.source.is_file())
            self.assertIsNotNone(db.get_video_transcript(video_id))

        with patch.object(transcripts, "cancel_transcript", side_effect=cancel) as cancel_work:
            library.cancel_and_delete(self.video_id)

        cancel_work.assert_called_once_with(self.video_id)
        self.assertFalse(self.media_dir.exists())
        self.assertIsNone(db.get_video(self.video_id))
        self.assertIsNone(db.get_video_transcript(self.video_id))


class TranscriptApiTest(TranscriptFixture):
    def setUp(self):
        super().setUp()
        # These route tests do not start background application workers.
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.endpoint = f"/api/media/{self.video_id}/transcript"

    def test_start_returns_accepted_status_and_engine_payload(self):
        payload = {"status": "queued", "source": None, "download_url": None}
        with patch.object(transcripts, "start_transcript", return_value=payload) as start:
            response = self.client.post(self.endpoint)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), payload)
        start.assert_called_once_with(self.video_id)

    def test_get_includes_prepared_transcript_text(self):
        payload = {"status": "completed", "source": "subtitles", "text": "A saved transcript."}
        with patch.object(transcripts, "transcript_payload", return_value=payload) as read:
            response = self.client.get(self.endpoint)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)
        read.assert_called_once_with(self.video_id, include_text=True)

    def test_download_streams_utf8_text_as_an_attachment(self):
        transcript = self.media_dir / "transcript.txt"
        transcript.write_text("Spoken words — café.\n", encoding="utf-8")
        with patch.object(transcripts, "transcript_file", return_value=transcript) as find:
            response = self.client.get(self.endpoint + "/download")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, transcript.read_bytes())
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertIn('attachment; filename="transcript.txt"', response.headers["content-disposition"])
        find.assert_called_once_with(self.video_id)

    def test_engine_errors_map_to_client_statuses(self):
        for method, suffix, service_name in (
                ("post", "", "start_transcript"),
                ("get", "", "transcript_payload"),
                ("get", "/download", "transcript_file")):
            for error, expected in ((LookupError("missing"), 404),
                                    (RuntimeError("not ready"), 409),
                                    (ValueError("invalid"), 400)):
                with self.subTest(method=method, suffix=suffix, status=expected):
                    with patch.object(transcripts, service_name, side_effect=error):
                        response = getattr(self.client, method)(self.endpoint + suffix)
                    self.assertEqual(response.status_code, expected, response.text)
                    self.assertEqual(response.json(), {"detail": str(error)})

    def test_media_payload_includes_metadata_without_inline_transcript_text(self):
        metadata = {"status": "completed", "source": "subtitles", "language": "en",
                    "download_url": self.endpoint + "/download"}
        with patch.object(transcripts, "transcript_payload", return_value=metadata) as read:
            response = self.client.get(f"/api/media/{self.video_id}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["transcript"], metadata)
        self.assertNotIn("text", response.json()["transcript"])
        read.assert_called_once_with(self.video_id, include_text=False)


if __name__ == "__main__":
    unittest.main()
