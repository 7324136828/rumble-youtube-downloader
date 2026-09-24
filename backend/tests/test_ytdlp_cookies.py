"""Offline regressions for locked Chromium databases and exported-cookie fallback."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JOBS_DB_PATH", str(Path(tempfile.mkdtemp()) / "test_jobs.db"))
os.environ.setdefault("MEDIA_ROOT", str(Path(tempfile.mkdtemp()) / "media"))

import yt_dlp
from app import config
from app.connectors import registry
from app.connectors.base import ConnectorError
from app.connectors.cookies import read_cookie_file
from app.connectors.ytdlp import _SingleVideoYoutubeDL

HEADER = "# Netscape HTTP Cookie File\n"
COOKIE = "#HttpOnly_.example.com\tTRUE\t/\tTRUE\t0\tsession\tfixture-secret\n"


class CookieFallbackTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.file = self.folder / "cookies.txt"
        self.file.write_text(HEADER + COOKIE, encoding="utf-8")
        self.video = self.folder / "fixture.mp4"
        self.video.write_bytes(b"fixture")
        self.connector = registry.get("youtube")
        self.environment = patch.object(config, "YTDLP_COOKIE_FILE", None)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def download(self, settings):
        return self.connector.download("https://youtu.be/fixture", self.folder, download_settings=settings)

    def test_file_mode_never_extracts_browser_cookies_and_never_rewrites_export(self):
        original = self.file.read_bytes()
        captured = []
        test = self

        def extract(ydl, url, download):
            test.assertNotIn("cookiesfrombrowser", ydl.params)
            test.assertEqual(ydl.cookiejar.get_cookie_header("https://example.com/"), "session=fixture-secret")
            # Simulate the site refreshing a session during a real download.
            for cookie in ydl.cookiejar:
                cookie.value = "updated-secret"
            captured.append(ydl.params["cookiefile"])
            return {"id": "fixture", "title": "Fixture", "filepath": str(test.video)}

        with patch("yt_dlp.cookies.extract_cookies_from_browser", side_effect=AssertionError("Browser touched")), \
                patch.object(_SingleVideoYoutubeDL, "extract_info", extract):
            for settings, environment in (
                ({"cookie_browser": "edge", "cookie_file": str(self.file)}, None),
                ({"cookie_browser": "edge"}, self.file),
                ({"cookie_browser": "edge", "cookie_file": str(self.file)}, self.folder / "missing.txt"),
            ):
                with self.subTest(settings=settings, environment=environment), \
                        patch.object(config, "YTDLP_COOKIE_FILE", environment):
                    self.assertEqual(self.download(settings).path, self.video.resolve())
                    self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(len(captured), 3)
        self.assertTrue(all(stream.closed for stream in captured))

    def test_clearing_file_resumes_selected_edge_profile(self):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = {"id": "fixture", "filepath": str(self.video)}
        with patch("app.connectors.ytdlp._SingleVideoYoutubeDL", return_value=ydl) as constructor:
            self.download({"cookie_file": "", "cookie_browser": "edge", "cookie_browser_profile": "Profile 2"})
        self.assertEqual(constructor.call_args.args[0]["cookiesfrombrowser"], ("edge", "Profile 2", None, None))
        self.assertNotIn("cookiefile", constructor.call_args.args[0])

    def test_invalid_exports_fail_without_logging_cookie_values_or_touching_browser(self):
        invalid_exports = (
            '{"cookies": "fixture-secret"}',
            HEADER + "fixture-secret",
            HEADER + COOKIE.replace("TRUE\t/", "FALSE\t/"),
            HEADER + COOKIE.replace("\t0\t", "\tinvalid-fixture-secret\t"),
            HEADER + COOKIE.replace("session", "sess\x00ion"),
            HEADER + COOKIE.replace("\tTRUE\t0", "\tINVALID\t0"),
        )
        for invalid in invalid_exports:
            with self.subTest(invalid=invalid):
                self.file.write_text(invalid, encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stderr(output), contextlib.redirect_stdout(output), \
                        patch("app.connectors.ytdlp._SingleVideoYoutubeDL") as constructor:
                    with self.assertRaisesRegex(ConnectorError, "Netscape") as caught:
                        self.download({"cookie_file": str(self.file), "cookie_browser": "edge"})
                constructor.assert_not_called()
                self.assertNotIn("fixture-secret", str(caught.exception))
                self.assertEqual(output.getvalue(), "")

    def test_file_read_failure_does_not_fall_back_to_browser_or_expose_path(self):
        with patch.object(Path, "open", side_effect=PermissionError("private-path")), \
                patch("app.connectors.ytdlp._SingleVideoYoutubeDL") as constructor:
            with self.assertRaisesRegex(ConnectorError, "permissions") as caught:
                self.download({"cookie_file": str(self.file), "cookie_browser": "edge"})
        constructor.assert_not_called()
        self.assertNotIn("private-path", str(caught.exception))

    def test_utf8_bom_and_windows_newlines_supported(self):
        self.file.write_bytes((HEADER + COOKIE).replace("\n", "\r\n").encode("utf-8-sig"))
        with read_cookie_file(self.file) as stream, yt_dlp.YoutubeDL({"cookiefile": stream, "quiet": True}) as ydl:
            self.assertEqual(ydl.cookiejar.get_cookie_header("https://example.com/"), "session=fixture-secret")

    def test_overlarge_and_binary_cookie_exports_rejected(self):
        with patch("app.connectors.cookies.MAX_COOKIE_FILE_BYTES", 10):
            with self.assertRaisesRegex(ConnectorError, "too large"):
                read_cookie_file(self.file)
        self.file.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(ConnectorError, "Netscape"):
            read_cookie_file(self.file)

    def fail_download(self, message, settings, warning=None):
        def extract(ydl, url, download):
            if warning:
                ydl.report_warning(warning)
            ydl.report_error(message)

        output = io.StringIO()
        with patch.object(_SingleVideoYoutubeDL, "extract_info", extract), contextlib.redirect_stderr(output):
            with self.assertRaises(ConnectorError) as caught:
                self.download(settings)
        self.assertEqual(output.getvalue(), "")
        return str(caught.exception)

    def test_chrome_database_error_correctly_explains_edge(self):
        error = self.fail_download("ERROR: Could not copy Chrome cookie database. See issue 7271", {"cookie_browser": "edge"})
        self.assertIn("Microsoft Edge", error)
        self.assertIn("Startup boost", error)
        self.assertIn("cookies.txt", error)
        self.assertNotIn("ERROR:", error)

    def test_wrapped_cookie_load_error_preserves_lock_cause(self):
        try:
            try:
                raise PermissionError("Could not copy Chrome cookie database")
            except PermissionError:
                raise yt_dlp.utils.DownloadError("failed to load cookies")
        except yt_dlp.utils.DownloadError as error:
            with patch("app.connectors.ytdlp._SingleVideoYoutubeDL", side_effect=error):
                with self.assertRaisesRegex(ConnectorError, "Microsoft Edge.*locked"):
                    self.download({"cookie_browser": "edge"})

    def test_decryption_warning_is_not_misreported_as_a_lock(self):
        error = self.fail_download("Sign in to confirm you’re not a bot", {"cookie_browser": "edge"},
                                   warning="Failed to decrypt with DPAPI")
        self.assertIn("could not decrypt Microsoft Edge", error)
        self.assertIn("cookies.txt", error)
        self.assertNotIn("locked", error)

    def test_missing_profile_has_actionable_error(self):
        error = self.fail_download("Could not find Chrome cookies database", {"cookie_browser": "edge"})
        self.assertIn("Microsoft Edge profile", error)

    def test_expired_export_does_not_tell_user_to_close_browser(self):
        error = self.fail_download("Sign in to confirm you’re not a bot", {"cookie_file": str(self.file), "cookie_browser": "edge"})
        self.assertIn("Export fresh", error)
        self.assertNotIn("close the browser", error)

    def test_unrelated_errors_still_visible_without_repeated_error_prefix(self):
        error = self.fail_download("ERROR: Video unavailable", {})
        self.assertEqual(error, "Video unavailable")


if __name__ == "__main__":
    unittest.main()
