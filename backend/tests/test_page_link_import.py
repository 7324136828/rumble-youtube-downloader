"""Bounded public-page link extraction and independent SQLite storage."""
import ipaddress
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.recommendations import router
from app.services import db, page_link_import as links


class PageLinkImportTest(unittest.TestCase):
    def test_page_fetch_limit_is_100_mib(self):
        self.assertEqual(links.MAX_PAGE_BYTES, 100 * 1024 * 1024)

    def test_extracts_relative_external_video_and_nonvideo_links(self):
        html = '''<a href="/video/1"> Video one </a><a href="https://other.test/article#part" title="Article"> </a>
            <a href="mailto:test@example.com">Email</a><a href="/video/1">Duplicate</a>'''
        with patch.object(links, "_request", return_value=(200, None, html)):
            result = links.extract_links("http://example.com/collection")
        self.assertEqual(result["page_url"], "https://example.com/collection")
        self.assertEqual(result["links"], [
            {"url": "https://example.com/video/1", "title": "Video one"},
            {"url": "https://other.test/article", "title": "Article"},
        ])

    def test_ignores_script_text_and_rejects_code_or_generic_link_titles(self):
        html = '''
            <a href="/with-title" title="Fallback title"><script>thl("omhmotk56f0",0);</script></a>
            <a href="/visible"><script>track(1);</script><span>Visible video title</span></a>
            <a href="/code-only">thl("omhmotk56f0",0);</a>
            <a href="/generic" title="Descriptive fallback">Video</a>
            <a href="/generic-only">VIDEO</a>
        '''
        with patch.object(links, "_request", return_value=(200, None, html)):
            result = links.extract_links("https://example.com/collection")
        self.assertEqual(result["links"], [
            {"url": "https://example.com/with-title", "title": "Fallback title"},
            {"url": "https://example.com/visible", "title": "Visible video title"},
            {"url": "https://example.com/code-only", "title": None},
            {"url": "https://example.com/generic", "title": "Descriptive fallback"},
            {"url": "https://example.com/generic-only", "title": None},
        ])

    def test_quality_badges_use_descriptive_labels_images_or_later_title_links(self):
        html = '''
            <a href="/labels" aria-label="Video" title="A day by the sea"><span>1440p</span><span>CC</span></a>
            <a href="/image"><img alt="Mountain sunrise"><span>1080p HD CC</span></a>
            <a href="/duplicate"><span>1440p</span><span>CC</span></a>
            <a href="/duplicate"><h3>Birds at dawn</h3></a>
            <a href="/missing">1440pCC</a>
            <a href="/words">How 1440p and CC work</a>
        '''
        with patch.object(links, "_request", return_value=(200, None, html)):
            result = links.extract_links("https://example.com/collection")
        self.assertEqual(result["links"], [
            {"url": "https://example.com/labels", "title": "A day by the sea"},
            {"url": "https://example.com/image", "title": "Mountain sunrise"},
            {"url": "https://example.com/duplicate", "title": "Birds at dawn"},
            {"url": "https://example.com/missing", "title": None},
            {"url": "https://example.com/words", "title": "How 1440p and CC work"},
        ])

    def test_later_title_links_enrich_existing_results_after_unique_link_limit(self):
        html = '<a href="/first">1440pCC</a><a href="/second">Another video</a><a href="/first">Actual title</a>'
        with patch.object(links, "_request", return_value=(200, None, html)), patch.object(links, "MAX_LINKS", 1):
            result = links.extract_links("https://example.com/collection")
        self.assertEqual(result["links"], [{"url": "https://example.com/first", "title": "Actual title"}])

    def test_hidden_nested_links_do_not_replace_visible_anchor(self):
        html = '<a href="/visible"><template><a href="/hidden">Hidden title</a></template>Actual title</a>'
        with patch.object(links, "_request", return_value=(200, None, html)):
            result = links.extract_links("https://example.com/collection")
        self.assertEqual(result["links"], [{"url": "https://example.com/visible", "title": "Actual title"}])

    def test_follows_bounded_public_redirects_and_rejects_bad_pages(self):
        with patch.object(links, "_request", side_effect=[(301, "https://other.test/list", None),
                                                          (200, None, '<a href="/a">A</a>')]) as request:
            result = links.extract_links("https://example.com/start")
        self.assertEqual(result["page_url"], "https://other.test/list")
        self.assertEqual(request.call_count, 2)
        for page in ("file:///secret", "https://user:pass@example.com/", "https://example.com/#part"):
            with self.subTest(page=page), self.assertRaises(links.PageLinkImportError):
                links.extract_links(page)
        with patch.object(links, "_request", return_value=(200, None, "<p>No links</p>")), \
                self.assertRaisesRegex(links.PageLinkImportError, "No HTTP"):
            links.extract_links("https://example.com/")

    def test_transport_pins_public_dns_and_disables_automatic_redirects(self):
        response = MagicMock(status_code=301, headers={"Location": "/next"})
        session = MagicMock()
        session.__enter__.return_value.get.return_value = response
        session_factory = MagicMock(return_value=session)
        with patch.object(links.custom_website_search, "_resolve_public_address",
                          return_value=ipaddress.ip_address("203.0.113.10")), \
                patch.object(links.requests, "Session", session_factory):
            status, location, body = links._request("https://example.com/start")
        self.assertEqual((status, location, body), (301, "/next", None))
        options = session_factory.call_args.kwargs["curl_options"]
        self.assertTrue(options)
        self.assertFalse(session.__enter__.return_value.get.call_args.kwargs["allow_redirects"])

    def test_oversized_page_reports_the_100_mib_limit(self):
        response = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
        response.iter_content.return_value = [b"1234", b"56"]
        session = MagicMock()
        session.__enter__.return_value.get.return_value = response
        with patch.object(links.custom_website_search, "_resolve_public_address",
                          return_value=ipaddress.ip_address("203.0.113.10")), \
                patch.object(links.requests, "Session", return_value=session), \
                patch.object(links, "MAX_PAGE_BYTES", 5), \
                self.assertRaisesRegex(links.PageLinkImportError, "100 MiB"):
            links._request("https://example.com/start")
        response.close.assert_called_once()


class PageLinkApiTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(config, "JOBS_DB_PATH", Path(folder) / "links.db"))
        db.init_db()
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def test_api_saves_lists_updates_and_removes_links_without_catalog_videos(self):
        extracted = {"page_url": "https://example.com/final", "links": [
            {"url": "https://example.com/video", "title": "Video"},
            {"url": "https://example.com/article", "title": "Article"}]}
        with patch.object(links, "extract_links", return_value=extracted):
            response = self.client.post("/api/recommendations/watch-later/save-all-links",
                                        json={"page_url": "https://example.com/start"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["added"], 2)
        payload = self.client.get("/api/recommendations/watch-later/links").json()
        self.assertEqual(payload["total"], 2)
        self.assertEqual({item["title"] for item in payload["items"]}, {"Video", "Article"})
        self.assertEqual(db.list_watch_later()["total"], 0)
        extracted["links"] = [{"url": "https://example.com/article", "title": "Updated article"}]
        with patch.object(links, "extract_links", return_value=extracted):
            updated = self.client.post("/api/recommendations/watch-later/save-all-links",
                                       json={"page_url": "https://example.com/final"}).json()
        self.assertEqual(updated["updated"], 1)
        item = next(item for item in self.client.get("/api/recommendations/watch-later/links").json()["items"]
                    if item["url"].endswith("/article"))
        self.assertEqual(item["title"], "Updated article")
        self.assertTrue(self.client.delete(f"/api/recommendations/watch-later/links/{item['id']}").json()["removed"])

    def test_preview_marks_video_links_without_writing_any_collection(self):
        extracted = {"page_url": "https://example.com/final", "links": [
            {"url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Video"},
            {"url": "https://example.com/article", "title": "Article"}]}
        with patch.object(links, "extract_links", return_value=extracted):
            response = self.client.post("/api/recommendations/watch-later/preview-links",
                                        json={"page_url": "https://example.com/start"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["total"], payload["video_total"]), (2, 1))
        self.assertTrue(payload["items"][0]["is_video"])
        self.assertEqual(payload["items"][0]["video_url"],
                         "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertFalse(payload["items"][1]["is_video"])
        self.assertEqual(db.list_watch_later()["total"], 0)
        self.assertEqual(db.list_watch_later_links()["total"], 0)


if __name__ == "__main__":
    unittest.main()
