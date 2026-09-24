"""Read-only title lookup never promotes provenance or follows off-site metadata."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.recommendation_providers import default_providers
from app.services import custom_website_search as custom
from app.services import recommendation_tools as tools, video_title_lookup as titles

PROVIDER = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
URL = "https://vimeo.com/123"
PUBLIC_PAGE_FETCH = custom.fetch_public_page


class VideoTitleLookupTest(unittest.TestCase):
    def setUp(self):
        self.page = self.enterContext(patch.object(custom, "fetch_public_page", side_effect=custom.CustomSearchError("Page unavailable")))
        self.native = self.enterContext(patch.object(tools, "extract_video_metadata", side_effect=tools.ToolError("Metadata unavailable")))

    def html(self, body, content_type="text/html"):
        self.page.side_effect = None
        self.page.return_value = (URL, body, content_type)

    def test_open_graph_title_wins_and_is_cleaned_without_other_requests(self):
        self.html('''<title>Page title</title><meta name="twitter:title" content="Twitter title">
            <meta property="og:title" content="  &lt;b&gt;Birds&lt;/b&gt; &amp; water ">
            <script>fetch("https://other.com/private")</script>''')
        self.assertEqual(titles.lookup_title("http://www.vimeo.com/123?utm_source=test", PROVIDER), "Birds & water")
        self.assertEqual(self.page.call_args.args[1], URL)
        self.native.assert_called_once()

    def test_page_metadata_accepts_safe_thumbnails_and_rejects_unrelated_hosts(self):
        same_site = titles.page_metadata(
            '<meta property="og:title" content="Birds">'
            '<meta property="og:image" content="/thumb.jpg">',
            "text/html", URL, PROVIDER)
        cdn = titles.page_metadata(
            '<meta name="twitter:image" content="https://i.vimeocdn.com/video/123.jpg">',
            "text/html", URL, PROVIDER)
        unrelated = titles.page_metadata(
            '<meta property="og:image" content="https://evil.example/track.jpg">',
            "text/html", URL, PROVIDER)
        self.assertEqual(same_site, {"title": "Birds", "thumbnail_url": "https://vimeo.com/thumb.jpg"})
        self.assertEqual(cdn["thumbnail_url"], "https://i.vimeocdn.com/video/123.jpg")
        self.assertIsNone(unrelated["thumbnail_url"])

    def test_page_metadata_accepts_configured_provider_cdn_and_lazy_metadata(self):
        provider = {"id": "videos.example-media.com", "name": "Example videos",
                    "domain": "videos.example-media.com", "enabled": True,
                    "thumbnail_domains": ["example-cdn.net"]}
        metadata = titles.page_metadata(
            '<meta itemprop="thumbnailUrl" content="https://thumb-v.example-cdn.net/a.jpg">',
            "text/html", "https://videos.example-media.com/videos/example", provider)
        self.assertEqual(metadata["thumbnail_url"], "https://thumb-v.example-cdn.net/a.jpg")

    def test_page_metadata_rejects_unconfigured_provider_cdn(self):
        provider = {"id": "videos.example-media.com", "name": "Example videos",
                    "domain": "videos.example-media.com", "enabled": True}
        metadata = titles.page_metadata(
            '<meta property="og:image" content="https://thumb-v.example-cdn.net/a.jpg">',
            "text/html", "https://videos.example-media.com/videos/example", provider)
        self.assertIsNone(metadata["thumbnail_url"])

    def test_twitter_structured_and_page_title_fallback_priority(self):
        cases = [('<meta property="og:title" content=""><meta name="twitter:title" content="Twitter birds"><title>Page birds</title>', "Twitter birds"),
                 ('<script type="application/ld+json">{"@type":"VideoObject","name":"Structured birds"}</script><title>Page birds</title>', "Structured birds"),
                 ("<title>  Birds\n by  water  </title>", "Birds by water"),
                 ("<title>" + "x" * 700 + "</title>", "x" * 500)]
        for body, expected in cases:
            with self.subTest(expected=expected[:30]):
                self.html(body)
                self.assertEqual(titles.lookup_title(URL, PROVIDER), expected)

    def test_videoobject_uses_current_canonical_url_and_ignores_related_video_names(self):
        document = {"@graph": [
            {"@type": "VideoObject", "url": "https://vimeo.com/456", "name": "Related video"},
            {"@type": "VideoObject", "url": "https://[malformed", "name": "Invalid URL"},
            {"@type": "https://schema.org/VideoObject", "url": "/123", "name": "Current video"}]}
        self.html(json.dumps(document), "application/ld+json")
        self.assertEqual(titles.lookup_title(URL, PROVIDER), "Current video")
        document = [{"@type": "VideoObject", "name": "First unknown"}, {"@type": "VideoObject", "name": "Second unknown"}]
        self.html('<script type="application/ld+json">' + json.dumps(document) + '</script><title>Actual page</title>')
        self.assertEqual(titles.lookup_title(URL, PROVIDER), "Actual page")

    def test_known_challenge_login_and_generic_website_titles_are_rejected(self):
        for value in ("Just a moment...", "Attention Required! | Cloudflare", "Log in • Instagram", "Sign in - Vimeo", "Vimeo"):
            with self.subTest(title=value), self.assertRaises(titles.TitleLookupError):
                self.html("<title>" + value + "</title>")
                titles.lookup_title(URL, PROVIDER)

    def test_placeholder_metadata_is_rejected_without_external_search_fallback(self):
        self.native.side_effect = None
        for value in ("1440pCC", "1080p HD CC", "4K / HDR / CC", "HDCC", "Video", 'thl("id",0);'):
            with self.subTest(title=value), self.assertRaises(titles.TitleLookupError):
                self.native.return_value = {"title": value}
                self.html('<meta property="og:title" content="1440pCC"><title>Video</title>')
                titles.lookup_title(URL, PROVIDER)

    def test_descriptive_titles_containing_quality_terms_remain_usable(self):
        self.native.side_effect = None
        for value in ("A walk in 4K", "Understanding 1440p and CC", "HD video restoration", "Video production tips"):
            with self.subTest(title=value):
                self.native.return_value = {"title": value}
                self.assertEqual(titles.lookup_title(URL, PROVIDER), value)
        self.page.assert_not_called()

    def test_builtins_reuse_metadata_only_lookup_without_generic_page_fetch(self):
        self.native.side_effect = None
        self.native.return_value = {"title": "Native birds", "verified": True}
        for provider, url in ((default_providers()[0], "https://youtu.be/abcdefghijk"),
                              (default_providers()[1], "https://rumble.com/v123abc-birds.html")):
            with self.subTest(provider=provider["id"]):
                self.assertEqual(titles.lookup_title(url, provider), "Native birds")
                self.assertEqual(self.native.call_args.kwargs["providers"], [provider])
        self.page.assert_not_called()

    def test_disabled_provider_lookup_does_not_change_original_settings(self):
        provider = {**PROVIDER, "enabled": False}
        self.html('<meta property="og:title" content="Saved birds">')
        self.assertEqual(titles.lookup_title(URL, provider), "Saved birds")
        self.assertFalse(provider["enabled"])
        self.assertTrue(self.page.call_args.args[0]["enabled"])

    def test_invalid_or_foreign_urls_never_start_discovery(self):
        for url in ("https://127.0.0.1/123", "file:///123", "https://other.com/123", "https://vimeo.com/channels/birds", "https://user:pass@vimeo.com/123"):
            with self.subTest(url=url), self.assertRaises(titles.TitleLookupError):
                titles.lookup_title(url, PROVIDER)
        self.page.assert_not_called()
        self.native.assert_not_called()

    def test_reusable_page_transport_rechecks_url_boundary_before_dns(self):
        with patch.object(custom.socket, "getaddrinfo") as dns, patch.object(custom.requests, "Session") as session:
            for url in ("https://other.com/123", "https://vimeo.com.evil.com/123", "https://127.0.0.1/123",
                        "https://user:pass@vimeo.com/123", "https://vimeo.com:8000/123", "http://vimeo.com/123",
                        "https://vimeo.com/123#fragment", "https://vimeo.com/%0a123"):
                with self.subTest(url=url), self.assertRaises(custom.CustomSearchError):
                    PUBLIC_PAGE_FETCH(PROVIDER, url)
        dns.assert_not_called()
        session.assert_not_called()

    def test_caller_cancellation_propagates_after_page_lookup(self):
        cancelled = False
        def guard():
            if cancelled:
                raise RuntimeError("lookup cancelled")
        def page(*args):
            nonlocal cancelled
            cancelled = True
            return URL, "<title>Birds</title>", "text/html"
        self.page.side_effect = page
        with self.assertRaisesRegex(RuntimeError, "lookup cancelled"):
            titles.lookup_title(URL, PROVIDER, guard)

    def test_expired_budget_stops_before_fallback_and_upstream_details_are_sanitized(self):
        self.html("<html>No metadata</html>")
        with patch.object(titles.time, "monotonic", side_effect=[0, 0, 34]), \
                self.assertRaisesRegex(titles.TitleLookupError, "timed out"):
            titles.lookup_title(URL, PROVIDER)
        self.page.side_effect = custom.CustomSearchError("secret cookie upstream details")
        with self.assertRaises(titles.TitleLookupError) as caught:
            titles.lookup_title(URL, PROVIDER)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
