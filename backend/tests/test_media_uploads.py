"""Local upload, artwork, and MP3 playback integration tests without network access."""
import io
import math
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
import zlib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.services import db, library  # noqa: E402


def _png(color):
    """Generate a small RGB PNG without an optional imaging dependency."""
    def chunk(kind, value):
        return (struct.pack(">I", len(value)) + kind + value
                + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff))

    pixels = b"".join(b"\0" + bytes(color) * 16 for _ in range(16))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class MediaPlaybackMigrationTest(unittest.TestCase):
    def test_existing_downloads_gain_default_playback_fields_without_losing_media(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        database = folder / "legacy.db"
        self.enterContext(patch.object(config, "JOBS_DB_PATH", database))
        old_schema = db._SCHEMA.replace("    media_kind TEXT NOT NULL DEFAULT 'video',\n", "")
        old_schema = old_schema.replace("    playback_format TEXT NOT NULL DEFAULT 'original',\n", "")
        old_schema = old_schema.replace("    playback_preference_explicit INTEGER NOT NULL DEFAULT 0,\n", "")
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript(old_schema)
        source = folder / "saved.mp4"
        source.write_bytes(b"original downloaded bytes")
        db.create_video("existing", "https://example.com/video", "generic", "best", folder)
        db.update_video("existing", status="ready", title="Saved before upgrade",
                        file_path=str(source), file_size=source.stat().st_size)

        db.init_db()
        db.init_db()

        row = db.get_video("existing")
        self.assertEqual(row["media_kind"], "video")
        self.assertEqual(row["playback_format"], "original")
        self.assertFalse(row["playback_preference_explicit"])
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["title"], "Saved before upgrade")
        self.assertEqual(row["file_path"], str(source))
        self.assertEqual(Path(row["file_path"]).read_bytes(), b"original downloaded bytes")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg and ffprobe required")
class MediaUploadsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        audio = io.BytesIO()
        with wave.open(audio, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(22050)
            stream.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(
                2 * math.pi * 440 * sample / 22050))) for sample in range(11025)))
        cls.wav = audio.getvalue()
        wav_path = cls.fixture_dir / "audio.wav"
        wav_path.write_bytes(cls.wav)
        mp4_path = cls.fixture_dir / "video.mp4"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=0.5",
             "-i", str(wav_path), "-shortest", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", str(mp4_path)],
            check=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        cls.mp4 = mp4_path.read_bytes()
        cls.red_artwork = _png((255, 0, 0))
        cls.blue_artwork = _png((0, 0, 255))

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.media_root = self.folder / "library"
        for name, path in (("JOBS_ROOT", self.folder),
                           ("JOBS_DB_PATH", self.folder / "jobs.db"),
                           ("MEDIA_ROOT", self.media_root)):
            self.enterContext(patch.object(config, name, path))
        # Keyword inference is unrelated to uploads and may use an external service.
        self.enterContext(patch.object(library.video_keywords, "schedule"))
        db.init_db()
        db.update_download_settings({"convert_for_browser": False,
                                     "generate_thumbnails": True})
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self, filename="recording.wav", content=None, thumbnail=None, title=None):
        files = {"file": (filename, self.wav if content is None else content,
                          "application/octet-stream")}
        if thumbnail is not None:
            files["thumbnail"] = ("cover.png", thumbnail, "image/png")
        response = self.client.post("/api/media/upload", files=files,
                                    data={"title": title} if title is not None else {})
        self.assertEqual(response.status_code, 200, response.text)
        initial = response.json()
        self.addCleanup(library.cancel_and_delete, initial["id"])
        ready = library.wait_for(initial["id"], 30)
        self.assertEqual(ready["status"], "ready", ready)
        return self.client.get(f"/api/media/{initial['id']}").json()

    def assert_empty_library(self):
        self.assertEqual(db.list_videos(), [])
        self.assertEqual(list(self.media_root.iterdir()) if self.media_root.exists() else [], [])

    def test_audio_upload_plays_mp3_with_custom_artwork_and_keeps_original(self):
        item = self.upload(thumbnail=self.red_artwork, title="My recording")
        self.assertEqual(item["connector"], "upload")
        self.assertEqual(item["title"], "My recording")
        self.assertEqual(item["media_kind"], "audio")
        self.assertEqual(item["playback_format"], "mp3")
        self.assertGreater(item["duration"], 0)
        self.assertNotIn("file_path", item)
        conversion = item["conversions"]["mp3"]
        self.assertEqual(conversion["status"], "completed")

        response = self.client.get(conversion["stream_url"], headers={"Range": "bytes=0-9"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertTrue(response.headers["content-range"].startswith("bytes 0-9/"))
        self.assertEqual(len(response.content), 10)
        complete = self.client.get(conversion["stream_url"])
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(response.content, complete.content[:10])
        suffix = self.client.get(conversion["stream_url"], headers={"Range": "bytes=-10"})
        self.assertEqual(suffix.status_code, 206)
        self.assertEqual(suffix.content, complete.content[-10:])

        original = self.client.get(item["download_url"])
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.content, self.wav)
        self.assertEqual(self.client.get(item["stream_url"]).content, self.wav)
        artwork = self.client.get(item["thumbnail_url"])
        self.assertEqual(artwork.status_code, 200)
        self.assertEqual(artwork.headers["content-type"], "image/jpeg")
        self.assertTrue(artwork.content.startswith(b"\xff\xd8"))
        self.assertIn(item["id"], [row["id"] for row in self.client.get("/api/media").json()])

    def test_video_conversion_selects_mp3_and_persists_playback_choice(self):
        item = self.upload("custom.mp4", self.mp4)
        video_id = item["id"]
        self.assertEqual(item["media_kind"], "video")
        self.assertEqual(item["playback_format"], "original")
        self.assertTrue(item["thumbnail_url"])
        response = self.client.patch(f"/api/media/{video_id}/playback", json={"format": "mp3"})
        self.assertEqual(response.status_code, 409)
        response = self.client.post(f"/api/media/{video_id}/conversions/mp3")
        self.assertEqual(response.status_code, 200, response.text)
        converted = library.wait_for_conversion(video_id, "mp3", 30)
        self.assertEqual(converted["status"], "completed", converted)
        selected = self.client.get(f"/api/media/{video_id}").json()
        self.assertEqual(selected["playback_format"], "mp3")
        self.assertEqual(selected["thumbnail_url"], item["thumbnail_url"])
        self.assertEqual(self.client.get(item["download_url"]).content, self.mp4)
        self.assertEqual(self.client.get(item["stream_url"]).content, self.mp4)

        for playback_format in ("original", "mp3"):
            response = self.client.patch(f"/api/media/{video_id}/playback",
                                         json={"format": playback_format})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["playback_format"], playback_format)
            self.assertTrue(response.json()["playback_preference_explicit"])
            db.init_db()
            self.assertEqual(db.get_video(video_id)["playback_format"], playback_format)

    def test_audio_converts_to_mp4_with_thumbnail_as_still_video(self):
        without_art = self.upload()
        response = self.client.post(
            f"/api/media/{without_art['id']}/conversions/mp4")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("thumbnail", response.json()["detail"].lower())
        self.assertIsNone(db.get_media_conversion(without_art["id"], "mp4"))

        item = self.upload(thumbnail=self.red_artwork, title="Still-image video")
        response = self.client.post(f"/api/media/{item['id']}/conversions/mp4")
        self.assertEqual(response.status_code, 200, response.text)
        converted = library.wait_for_conversion(item["id"], "mp4", 30)
        self.assertEqual(converted["status"], "completed", converted)
        current = self.client.get(f"/api/media/{item['id']}").json()
        mp4 = current["conversions"]["mp4"]
        self.assertEqual(mp4["status"], "completed")
        self.assertTrue(mp4["download_url"])
        self.assertTrue(mp4["stream_url"])
        self.assertEqual(current["playback_format"], "mp3")

        output = Path(db.get_media_conversion(item["id"], "mp4")["output_path"])
        streams = library.media.probe_streams(output)
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        audio = next(stream for stream in streams if stream["codec_type"] == "audio")
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual((video["width"], video["height"]), (16, 16))
        self.assertEqual(audio["codec_name"], "aac")
        rendered = self.client.get(mp4["download_url"])
        self.assertEqual(rendered.status_code, 200)
        self.assertEqual(rendered.headers["content-type"], "video/mp4")
        self.assertEqual(rendered.content, output.read_bytes())
        self.assertEqual(self.client.get(item["download_url"]).content, self.wav)

    def test_thumbnail_replacement_updates_cache_url_and_rejects_invalid_image(self):
        item = self.upload(thumbnail=self.red_artwork)
        original_image = self.client.get(item["thumbnail_url"]).content
        response = self.client.post(f"/api/media/{item['id']}/thumbnail", files={
            "thumbnail": ("new.png", self.blue_artwork, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        replaced = response.json()
        self.assertNotEqual(replaced["thumbnail_url"], item["thumbnail_url"])
        updated_image = self.client.get(replaced["thumbnail_url"])
        self.assertEqual(updated_image.headers["content-type"], "image/jpeg")
        self.assertNotEqual(updated_image.content, original_image)
        saved_path = Path(db.get_video(item["id"])["thumbnail_path"])
        response = self.client.post(f"/api/media/{item['id']}/thumbnail", files={
            "thumbnail": ("broken.png", b"not an image", "image/png")})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(saved_path.read_bytes(), updated_image.content)
        current = self.client.get(f"/api/media/{item['id']}").json()
        self.assertEqual(current["thumbnail_url"], replaced["thumbnail_url"])
        self.assertEqual(current["playback_format"], "mp3")

    def test_invalid_media_and_still_images_do_not_create_library_items(self):
        for filename, content in (("fake.mp4", b"this is not media"),
                                  ("empty.wav", b""), ("picture.png", self.red_artwork)):
            with self.subTest(filename=filename):
                response = self.client.post("/api/media/upload", files={
                    "file": (filename, content, "video/mp4")})
                self.assertEqual(response.status_code, 400, response.text)
                self.assert_empty_library()

    def test_invalid_initial_thumbnail_rejects_upload_without_orphan_files(self):
        response = self.client.post("/api/media/upload", files={
            "file": ("recording.wav", self.wav, "audio/wav"),
            "thumbnail": ("bad.png", b"invalid image bytes", "image/png")})
        self.assertEqual(response.status_code, 400, response.text)
        self.assert_empty_library()

    def test_oversized_upload_is_rejected_without_orphan_files(self):
        with patch.object(config, "MAX_UPLOAD_BYTES", 100):
            response = self.client.post("/api/media/upload", files={
                "file": ("recording.wav", self.wav, "audio/wav")})
        self.assertEqual(response.status_code, 413, response.text)
        self.assert_empty_library()

    def test_upload_uses_actual_audio_format_and_contains_untrusted_filename(self):
        item = self.upload("../../custom.mp4", self.wav)
        self.assertEqual(item["media_kind"], "audio")
        self.assertEqual(item["playback_format"], "mp3")
        self.assertEqual(item["title"], "custom")
        row = db.get_video(item["id"])
        media_dir = Path(row["media_dir"]).resolve()
        original = Path(row["file_path"]).resolve()
        converted = Path(db.get_media_conversion(item["id"], "mp3")["output_path"]).resolve()
        self.assertTrue(media_dir.is_relative_to(self.media_root.resolve()))
        self.assertTrue(original.is_relative_to(media_dir))
        self.assertTrue(converted.is_relative_to(media_dir))
        self.assertEqual(original.suffix, ".wav")
        self.assertEqual(original.read_bytes(), self.wav)
        self.assertEqual(self.client.get(item["download_url"]).content, self.wav)
        self.assertEqual(list(self.media_root.iterdir()), [media_dir])
        self.assertFalse((self.folder / "custom.mp4").exists())

    def test_deleting_queued_upload_or_conversion_cancels_without_starting_ffmpeg(self):
        for work in ("upload", "conversion"):
            with self.subTest(work=work):
                if work == "conversion":
                    item = self.upload("source.mp4", self.mp4)
                    video_id = item["id"]
                    # Ensure the ready upload has released its original worker slot.
                    with library._LOCK:
                        entry = library._ACTIVE.get(video_id)
                    if entry:
                        entry["thread"].join(timeout=5)
                        self.assertFalse(entry["thread"].is_alive())
                with patch.object(library, "_SLOTS", threading.Semaphore(0)), \
                        patch.object(library.media, "convert_to_mp3") as convert:
                    if work == "upload":
                        response = self.client.post("/api/media/upload", files={
                            "file": ("queued.wav", self.wav, "audio/wav")})
                        self.assertEqual(response.status_code, 200, response.text)
                        video_id = response.json()["id"]
                        self.addCleanup(library.cancel_and_delete, video_id)
                    else:
                        response = self.client.post(f"/api/media/{video_id}/conversions/mp3")
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json()["status"], "queued")
                    try:
                        row = db.get_video(video_id)
                        with library._LOCK:
                            worker = (library._ACTIVE[video_id] if work == "upload"
                                      else library._CONVERSIONS[(video_id, "mp3")])["thread"]
                        started = time.monotonic()
                        response = self.client.delete(f"/api/media/{video_id}")
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertLess(time.monotonic() - started, 3)
                        self.assertFalse(worker.is_alive())
                        convert.assert_not_called()
                        self.assertFalse(Path(row["media_dir"]).exists())
                        self.assertIsNone(db.get_video(video_id))
                        self.assertIsNone(db.get_media_conversion(video_id, "mp3"))
                        self.assert_empty_library()
                    finally:
                        # Keep the zero-slot semaphore installed until cleanup completes.
                        library.cancel_and_delete(video_id)

    def test_failed_mp3_conversion_keeps_original_playback_and_can_be_retried(self):
        item = self.upload("source.mp4", self.mp4)
        video_id = item["id"]
        with patch.object(library.media, "convert_to_mp3", return_value=False):
            response = self.client.post(f"/api/media/{video_id}/conversions/mp3")
            self.assertEqual(response.status_code, 200, response.text)
            failed = library.wait_for_conversion(video_id, "mp3", 5)
            self.assertEqual(failed["status"], "failed", failed)
            with library._LOCK:
                entry = library._CONVERSIONS.get((video_id, "mp3"))
            if entry:
                entry["thread"].join(timeout=5)
                self.assertFalse(entry["thread"].is_alive())
        current = self.client.get(f"/api/media/{video_id}").json()
        self.assertEqual(current["status"], "ready")
        self.assertEqual(current["playback_format"], "original")
        self.assertEqual(current["conversions"]["mp3"]["status"], "failed")
        self.assertIsNone(current["conversions"]["mp3"]["stream_url"])
        self.assertTrue(current["conversions"]["mp3"]["error_message"])
        self.assertEqual(self.client.get(current["stream_url"]).content, self.mp4)
        self.assertEqual(self.client.get(current["download_url"]).content, self.mp4)
        response = self.client.post(f"/api/media/{video_id}/conversions/mp3")
        self.assertEqual(response.status_code, 200, response.text)
        retried = library.wait_for_conversion(video_id, "mp3", 30)
        self.assertEqual(retried["status"], "completed", retried)
        selected = self.client.get(f"/api/media/{video_id}").json()
        self.assertEqual(selected["playback_format"], "mp3")
        self.assertEqual(self.client.get(selected["download_url"]).content, self.mp4)

    def test_mp3_upload_preserves_bytes_without_transcoding(self):
        prepared = self.upload()
        mp3 = self.client.get(prepared["conversions"]["mp3"]["stream_url"]).content
        with patch.object(library.media, "convert_to_mp3") as convert:
            item = self.upload("custom.mp3", mp3)
            convert.assert_not_called()
        self.assertEqual(item["media_kind"], "audio")
        self.assertEqual(item["playback_format"], "mp3")
        self.assertEqual(item["conversions"]["mp3"]["status"], "completed")
        self.assertEqual(self.client.get(item["download_url"]).content, mp3)
        self.assertEqual(self.client.get(item["conversions"]["mp3"]["stream_url"]).content, mp3)

    def test_delete_removes_uploaded_original_mp3_and_artwork(self):
        item = self.upload(thumbnail=self.red_artwork)
        row = db.get_video(item["id"])
        conversion = db.get_media_conversion(item["id"], "mp3")
        paths = [Path(row["file_path"]), Path(row["thumbnail_path"]),
                 Path(conversion["output_path"])]
        self.assertTrue(all(path.is_file() for path in paths))
        response = self.client.delete(f"/api/media/{item['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["deleted"])
        self.assertFalse(Path(row["media_dir"]).exists())
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertIsNone(db.get_media_conversion(item["id"], "mp3"))
        self.assertEqual(self.client.get(item["conversions"]["mp3"]["stream_url"]).status_code, 404)

    def test_retention_purges_uploaded_original_and_derived_audio(self):
        item = self.upload(thumbnail=self.red_artwork)
        row = db.get_video(item["id"])
        self.assertIsNotNone(row["expires_at"])
        db.update_video(item["id"], completed_at="2000-01-01T00:00:00+00:00")
        self.assertEqual(library.purge_expired(), [item["id"]])
        self.assertFalse(Path(row["media_dir"]).exists())
        self.assertIsNone(db.get_video(item["id"]))
        self.assertIsNone(db.get_media_conversion(item["id"], "mp3"))


if __name__ == "__main__":
    unittest.main()
