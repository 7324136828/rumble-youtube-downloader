"""Offline provider and API checks for metadata-only video search."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.schemas.search import SearchResult  # noqa: E402
from app.services import search  # noqa: E402


# Structure observed on https://rumble.com/search/video?q=nature, with fixture
# content substituted. Rumble emits unquoted attributes and SVG title badges.
RUMBLE_CARD = """
<li class="video-listing-entry"><article class="video-item ">
  <a class=video-item--a href="/v123abc-nature.html?tracking=1&amp;x=2">
    <div class=video-item--img-wrapper>
      <img class=video-item--img src="https://hugh.cdn.rumble.cloud/video/nature.jpg" alt="Nature">
    </div><span class=video-item--duration data-value="1:02:03"></span>
  </a><div class=video-item--info>
    <h3 class=video-item--title>Nature &amp; <b>wildlife</b></h3>
    <address><a class=video-item--by-a href=/c/nature>
      <div>Nature Channel<svg><title>Verified</title><path /></svg></div>
    </a></address>
  </div>
</article></li>
"""


def result(source, number=1):
    return SearchResult(id=f"{source}:{number}", title=f"Video {number}",
                        connector=source, source_url=f"https://{source}.com/video-{number}")


class YouTubeSearchTest(unittest.TestCase):
    def test_flat_metadata_search_normalizes_and_deduplicates(self):
        entry = {"id": "abcdefghijk", "title": "  Nature   walk ", "channel": "Guide",
                 "duration": 83, "url": "https://evil.test/ignore-this",
                 "thumbnails": [{"url": "javascript:alert(1)"},
                                {"url": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"}]}
        entries = [entry, entry, {"id": "../../unsafe", "title": "Bad"},
                   {"id": "12345678901", "title": "Playlist", "_type": "playlist"},
                   {"id": "ABCDEFGHIJK", "title": "Live", "duration": float("nan")}]
        fake_process = subprocess.CompletedProcess([], 0, json.dumps({"entries": entries}), "")
        with patch.object(search.subprocess, "run", return_value=fake_process) as run:
            videos = search.search_youtube("nature & birds", 12)
        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0].source_url, "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(videos[0].title, "Nature walk")
        self.assertEqual(videos[0].uploader, "Guide")
        self.assertEqual(videos[0].duration, 83)
        self.assertTrue(videos[0].thumbnail_url.startswith("https://i.ytimg.com/"))
        self.assertIsNone(videos[1].duration)
        command = run.call_args.args[0]
        self.assertIn("--flat-playlist", command)
        self.assertIn("--skip-download", command)
        self.assertIn("--ignore-config", command)
        self.assertIn("--no-cache-dir", command)
        self.assertEqual(command[-1], "ytsearch12:nature & birds")
        self.assertEqual(run.call_args.kwargs["timeout"], search.SEARCH_TIMEOUT)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_timeout_and_upstream_errors(self):
        with patch.object(search.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 25)):
            with self.assertRaisesRegex(search.SearchError, "timed out"):
                search.search_youtube("nature", 3)
        with patch.object(search.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "internal")):
            with self.assertRaisesRegex(search.SearchError, "unavailable"):
                search.search_youtube("nature", 3)

    def test_empty_results_are_successful_but_invalid_payload_is_error(self):
        for payload in ('{"entries": []}', '{}', 'not json', 'null'):
            with self.subTest(payload=payload):
                with patch.object(search.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload, "")):
                    if payload == '{"entries": []}':
                        self.assertEqual(search.search_youtube("nothing", 3), [])
                    else:
                        with self.assertRaises(search.SearchError):
                            search.search_youtube("nothing", 3)


class RumbleSearchTest(unittest.TestCase):
    def _response(self, page, status=200):
        response = MagicMock(status_code=status)
        response.iter_content.return_value = [page.encode()]
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = response
        return session, response

    def test_current_page_markup_metadata_and_limits(self):
        page = '<a href="/vnotresult-sidebar.html">Sidebar</a>' + RUMBLE_CARD * 2
        page += RUMBLE_CARD.replace("v123abc", "v456def")
        session, response = self._response(page)
        with patch.object(search.requests, "Session", return_value=session) as constructor:
            videos = search.search_rumble("nature & birds", 1)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].id, "rumble:v123abc")
        self.assertEqual(videos[0].title, "Nature & wildlife")
        self.assertEqual(videos[0].uploader, "Nature Channel")
        self.assertEqual(videos[0].duration, 3723)
        self.assertEqual(videos[0].source_url, "https://rumble.com/v123abc-nature.html")
        self.assertIn("rumble.cloud", videos[0].thumbnail_url)
        self.assertEqual(constructor.call_args.kwargs["curl_options"][search.CurlOpt.TIMEOUT_MS], 15000)
        self.assertEqual(session.get.call_args.args[0],
                         "https://rumble.com/search/video?q=nature%20%26%20birds")
        self.assertNotIn("params", session.get.call_args.kwargs)
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        response.close.assert_called_once()

    def test_query_encoding_avoids_rumble_canonical_redirect(self):
        # Rumble redirects '+'-encoded spaces to '%20'. Send the canonical URL
        # directly while retaining the existing restriction on redirects.
        queries = (("cooking science", "cooking%20science"),
                   ("C++ & science", "C%2B%2B%20%26%20science"),
                   ("caf\u00e9 \u9ce5", "caf%C3%A9%20%E9%B3%A5"))
        for query, encoded in queries:
            with self.subTest(query=query):
                session, response = self._response(RUMBLE_CARD)
                with patch.object(search.requests, "Session", return_value=session):
                    videos = search.search_rumble(query, 3)
                self.assertEqual(len(videos), 1)
                self.assertEqual(session.get.call_args.args[0],
                                 "https://rumble.com/search/video?q=" + encoded)
                self.assertNotIn("params", session.get.call_args.kwargs)
                self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
                response.close.assert_called_once()

    def test_only_safe_platform_video_links(self):
        parser = search._RumbleResultsParser(12)
        for href in ("https://rumble.com.evil.test/v123abc.html", "javascript:alert(1)",
                     "/c/channel", "https://user:pass@rumble.com/v123abc.html",
                     "//evil.test/v123abc.html", "https://rumble.com:invalid/v123abc.html"):
            parser.feed(RUMBLE_CARD.replace("/v123abc-nature.html?tracking=1&amp;x=2", href))
        self.assertEqual(parser.results, [])
        parser.feed(RUMBLE_CARD * 2)
        self.assertEqual(len(parser.results), 1)

    def test_missing_optional_metadata_is_supported(self):
        parser = search._RumbleResultsParser(12)
        parser.feed('<article class=video-item><a class=video-item--a href=/v123abc-clip.html></a>'
                    '<h3 class=video-item--title>Live now</h3></article>')
        video = parser.results[0]
        self.assertIsNone(video.duration)
        self.assertIsNone(video.uploader)
        self.assertIsNone(video.thumbnail_url)

    def test_empty_results_challenge_and_http_failure(self):
        for page, status, valid in (("<div>No videos found</div>", 200, True),
                                    ("<title>Just a moment...</title>", 200, False),
                                    ("<div>No videos found</div>", 403, False)):
            with self.subTest(page=page, status=status):
                session, response = self._response(page, status)
                with patch.object(search.requests, "Session", return_value=session):
                    if valid:
                        self.assertEqual(search.search_rumble("nothing", 3), [])
                    else:
                        with self.assertRaises(search.SearchError):
                            search.search_rumble("nothing", 3)
                response.close.assert_called_once()

    def test_large_response_stops_and_closes(self):
        session, response = self._response("x" * 100)
        with patch.object(search.requests, "Session", return_value=session), patch.object(search, "MAX_PAGE_BYTES", 50):
            with self.assertRaisesRegex(search.SearchError, "large"):
                search.search_rumble("nature", 3)
        response.close.assert_called_once()

    def test_http_failure_reports_status_in_warning_and_log(self):
        session, response = self._response("private upstream response", 429)
        with patch.object(search.requests, "Session", return_value=session), \
                patch.object(search, "search_youtube", return_value=[result("youtube")]), \
                self.assertLogs(search._LOG, level="WARNING") as logged:
            found = search.search_videos("nature", limit=3)
        self.assertEqual([item.connector for item in found.results], ["youtube"])
        self.assertEqual(len(found.warnings), 1)
        self.assertEqual(found.warnings[0].source, "rumble")
        self.assertRegex(found.warnings[0].message, r"HTTP\s?429")
        self.assertRegex(" ".join(logged.output), r"rumble search failed:.*HTTP\s?429")
        self.assertNotIn("private upstream response", found.warnings[0].message)
        self.assertNotIn("private upstream response", " ".join(logged.output))
        response.close.assert_called_once()


class SearchApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_both_sources_interleave_and_bound_without_downloads(self):
        with patch.object(search, "search_youtube", return_value=[result("youtube", n) for n in range(4)]) as youtube, \
                patch.object(search, "search_rumble", return_value=[result("rumble", n) for n in range(4)]), \
                patch("app.main.library.start_download") as download:
            response = self.client.get("/api/search", params={"q": "  nature  ", "limit": 3})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["query"], "nature")
        self.assertEqual(payload["source"], "all")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual([item["connector"] for item in payload["results"]], ["youtube", "rumble", "youtube"])
        youtube.assert_called_once_with("nature", 3)
        download.assert_not_called()

    def test_selected_source_does_not_call_other_provider(self):
        with patch.object(search, "search_youtube", return_value=[]) as youtube, \
                patch.object(search, "search_rumble") as rumble:
            response = self.client.get("/api/search", params={"q": "nature", "source": "youtube"})
        self.assertEqual(response.status_code, 200)
        youtube.assert_called_once_with("nature", 12)
        rumble.assert_not_called()

    def test_partial_failure_keeps_available_results_and_warning(self):
        with patch.object(search, "search_youtube", side_effect=search.SearchError("YouTube search timed out.")), \
                patch.object(search, "search_rumble", return_value=[result("rumble")]):
            response = self.client.get("/api/search", params={"q": "nature"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["connector"], "rumble")
        self.assertEqual(response.json()["warnings"], [{"source": "youtube", "message": "YouTube search timed out."}])

    def test_total_failure_returns_readable_error(self):
        with patch.object(search, "search_youtube", side_effect=search.SearchError("YouTube search timed out.")), \
                patch.object(search, "search_rumble", side_effect=RuntimeError("private internal error")), \
                self.assertLogs(search._LOG, level="WARNING") as logged:
            response = self.client.get("/api/search", params={"q": "nature"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("YouTube search timed out", response.json()["detail"])
        self.assertIn("Rumble search is unavailable", response.json()["detail"])
        self.assertNotIn("private", response.json()["detail"])
        self.assertNotIn("private internal error", " ".join(logged.output))
        self.assertIn("rumble search failed: Rumble search is unavailable", " ".join(logged.output))

    def test_invalid_queries_never_start_search(self):
        with patch.object(search, "search_videos") as provider:
            for params in ({}, {"q": ""}, {"q": "   "}, {"q": "a" * 201},
                           {"q": "nature", "source": "other"},
                           {"q": "nature", "limit": 0}, {"q": "nature", "limit": 25}):
                with self.subTest(params=params):
                    self.assertIn(self.client.get("/api/search", params=params).status_code, (400, 422))
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
