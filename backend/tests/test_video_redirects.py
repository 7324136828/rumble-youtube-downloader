"""Offline redirect resolution before Watch later persistence."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.recommendation_providers import default_providers
from app.services import db, video_redirects

VIMEO = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
PROVIDERS = default_providers() + [VIMEO]


class VideoRedirectTest(unittest.TestCase):
    def test_follows_relative_and_cross_configured_redirects_to_canonical_video(self):
        with patch.object(video_redirects, "_probe", side_effect=[
                (301, "/video/456?utm_source=old"), (302, "https://youtu.be/abcdefghijk"), (200, None)]) as probe:
            result = video_redirects.resolve_video_url("http://www.vimeo.com/123?utm_source=input", PROVIDERS)
        self.assertEqual(result, "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertEqual(probe.call_count, 3)

    def test_rejects_redirects_outside_configured_websites_or_to_nonvideos(self):
        for location in ("https://evil.example/video/1", "http://vimeo.com/456", "https://vimeo.com/search"):
            with self.subTest(location=location), patch.object(video_redirects, "_probe", return_value=(301, location)), \
                    self.assertRaises(video_redirects.RedirectResolutionError):
                video_redirects.resolve_video_url("https://vimeo.com/123", PROVIDERS)

    def test_follows_distinct_redirect_url_for_the_same_canonical_video(self):
        url = "https://www.youtube.com/watch?v=abcdefghijk"
        with patch.object(video_redirects, "_probe", side_effect=[
                (302, url + "&utm_source=redirect"), (200, None)]) as probe:
            result = video_redirects.resolve_video_url(url, PROVIDERS)
        self.assertEqual(result, url)
        self.assertEqual(probe.call_count, 2)

    def test_rejects_loops_missing_locations_and_excessive_hops(self):
        cases = [[(301, "/1")], [(301, None)], [(301, f"/{number}") for number in range(2, 9)]]
        for replies in cases:
            with self.subTest(replies=replies), patch.object(video_redirects, "_probe", side_effect=replies), \
                    self.assertRaises(video_redirects.RedirectResolutionError):
                video_redirects.resolve_video_url("https://vimeo.com/1", PROVIDERS)

    def test_unavailable_probe_preserves_canonical_input(self):
        with patch.object(video_redirects, "_probe", side_effect=video_redirects.RedirectUnavailable("TLS unavailable")):
            result = video_redirects.resolve_video_url("http://www.vimeo.com/123?utm_source=input", PROVIDERS)
        self.assertEqual(result, "https://vimeo.com/123")

        with patch.object(video_redirects.custom_website_search, "_resolve_public_address",
                          side_effect=video_redirects.custom_website_search.CustomSearchError("DNS unavailable")):
            result = video_redirects.resolve_video_url("https://vimeo.com/123", PROVIDERS)
        self.assertEqual(result, "https://vimeo.com/123")

    def test_batch_resolves_before_storage_and_preserves_metadata(self):
        videos = [{"source_url": "https://vimeo.com/1", "title": "Mine"},
                  {"source_url": "https://vimeo.com/2"}]
        with patch.object(video_redirects, "resolve_video_url", side_effect=["https://vimeo.com/9", "https://vimeo.com/9"]):
            result = video_redirects.resolve_import(videos, PROVIDERS)
        self.assertEqual([item["source_url"] for item in result], ["https://vimeo.com/9"] * 2)
        self.assertEqual(result[0]["title"], "Mine")

    def test_transport_checks_public_dns_and_never_auto_follows(self):
        response = unittest.mock.MagicMock(status_code=301, headers={"Location": "/456"})
        session = unittest.mock.MagicMock()
        session.__enter__.return_value.get.return_value = response
        with patch.object(video_redirects.custom_website_search, "_resolve_public_address",
                          return_value=__import__("ipaddress").ip_address("203.0.113.10")), \
                patch.object(video_redirects.requests, "Session", return_value=session):
            status, location = video_redirects._probe("https://vimeo.com/123", VIMEO, lambda: None)
        self.assertEqual((status, location), (301, "/456"))
        self.assertFalse(session.__enter__.return_value.get.call_args.kwargs["allow_redirects"])
        self.assertTrue(session.__enter__.return_value.get.call_args.kwargs["discard_cookies"])

    def test_api_passes_resolved_url_to_database(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.routers.recommendations import router
        app = FastAPI()
        app.include_router(router)
        stored = {"items": [], "added": 1, "updated": 0, "revision": 1}
        with TestClient(app) as client, \
                patch.object(db, "get_recommendation_settings", return_value={"providers": PROVIDERS}), \
                patch.object(video_redirects, "resolve_import",
                             return_value=[{"source_url": "https://vimeo.com/456"}]) as resolve, \
                patch.object(db, "add_watch_later", return_value=stored) as add, \
                patch("app.services.watch_later_titles.schedule_items", side_effect=lambda items: items):
            response = client.post("/api/recommendations/watch-later",
                                   json={"videos": [{"source_url": "https://vimeo.com/123"}]})
        self.assertEqual(response.status_code, 200)
        resolve.assert_called_once()
        self.assertEqual(add.call_args.args[0], [{"source_url": "https://vimeo.com/456"}])


if __name__ == "__main__":
    unittest.main()
