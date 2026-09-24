"""Offline tests of configured search templates, transport boundaries, and metadata."""
import json
import socket
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.recommendation_providers import RecommendationProvider
from app.services import custom_website_search as custom


def website(template="https://vimeo.com/?search={query}"):
    return RecommendationProvider(name="Vimeo", domain="vimeo.com", search_url=template).model_dump()


def address(value="93.184.216.34"):
    family = socket.AF_INET6 if ":" in value else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (value, 443))


class CustomWebsiteSearchTest(unittest.TestCase):
    def setUp(self):
        self.resolve = self.enterContext(patch.object(custom.socket, "getaddrinfo", return_value=[address()]))
        self.factory = self.enterContext(patch.object(custom.requests, "Session"))
        self.session = self.factory.return_value.__enter__.return_value

    @contextmanager
    def page(self, body, status=200, content_type="text/html"):
        response = MagicMock(status_code=status, headers={"Content-Type": content_type})
        response.iter_content.return_value = [body if isinstance(body, bytes) else body.encode()]
        self.session.get.return_value = response
        yield response

    def test_public_address_is_pinned_proxies_redirects_and_credentials_disabled(self):
        with self.page('<a href="/12345">Birds</a>') as response:
            result = custom.search_custom(website(), "birds & water/air", 3)
        self.assertEqual(result["results"][0]["source_url"], "https://vimeo.com/12345")
        self.assertFalse(result["results"][0]["verified"])
        self.assertEqual(result["results"][0]["verification"], "custom_search")
        self.assertEqual(result["results"][0]["origins"], ["custom_search"])
        options = self.factory.call_args.kwargs
        self.assertFalse(options["trust_env"])
        curl = options["curl_options"]
        self.assertEqual(curl[custom.CurlOpt.RESOLVE], [b"vimeo.com:443:93.184.216.34"])
        self.assertEqual(curl[custom.CurlOpt.PROXY], "")
        self.assertEqual(curl[custom.CurlOpt.NOPROXY], "*")
        self.assertEqual(curl[custom.CurlOpt.NETRC], 0)
        self.assertEqual(curl[custom.CurlOpt.TIMEOUT_MS], 12000)
        self.assertEqual(parse_qs(urlsplit(self.session.get.call_args.args[0]).query), {"search": ["birds & water/air"]})
        kwargs = self.session.get.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["verify"])
        self.assertTrue(kwargs["discard_cookies"])
        self.assertNotIn("cookies", kwargs)
        self.assertNotIn("auth", kwargs)
        response.close.assert_called_once()
        self.resolve.assert_called_once_with("vimeo.com", 443, 0, socket.SOCK_STREAM)

    def test_path_placeholder_and_ipv6_address_are_safely_encoded(self):
        self.resolve.return_value = [address("2606:4700:4700::1111")]
        with self.page('<a href="https://vimeo.com/123">Video</a>'):
            custom.search_custom(website("https://search.vimeo.com/find/{query}?sort=new"), "water / birds?", 2)
        self.assertEqual(self.session.get.call_args.args[0], "https://search.vimeo.com/find/water%20%2F%20birds%3F?sort=new")
        self.assertEqual(self.factory.call_args.kwargs["curl_options"][custom.CurlOpt.RESOLVE],
                         [b"search.vimeo.com:443:[2606:4700:4700::1111]"])

    def test_accidental_root_double_slash_is_normalized_and_request_is_logged(self):
        with self.page('<a href="/12345">Birds</a>'), \
                self.assertLogs(custom._OUTBOUND_LOG, level="INFO") as logs:
            custom.search_custom(website("https://vimeo.com//?search={query}"), "birds", 3)
        self.assertEqual(self.session.get.call_args.args[0], "https://vimeo.com/?search=birds")
        output = "\n".join(logs.output)
        self.assertIn("External search request: GET https://vimeo.com/?search=birds", output)
        self.assertIn("HTTP 200", output)

    def test_private_mixed_or_transition_dns_answers_never_receive_requests(self):
        for value in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1",
                      "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::"):
            with self.subTest(address=value):
                self.resolve.return_value = [address(), address(value)]
                with self.assertRaisesRegex(custom.CustomSearchError, "public addresses"):
                    custom.search_custom(website(), "nature", 3)
        self.factory.assert_not_called()

    def test_invalid_template_revalidated_before_dns(self):
        for template in ("https://other.com/?q={query}", "https://vimeo.com@127.0.0.1/?q={query}",
                         "https://vimeo.com:8000/?q={query}", "https://vimeo.com/?q={query}%0d%0aX", None):
            with self.subTest(template=template), self.assertRaises(custom.CustomSearchError):
                custom.search_custom({**website(), "search_url": template}, "nature", 3)
        self.resolve.assert_not_called()
        self.factory.assert_not_called()

    def test_dns_timeout_and_cancellation_do_not_start_http(self):
        future = MagicMock()
        with patch.object(custom._DNS_POOL, "submit", return_value=future), \
                patch.object(custom.time, "monotonic", side_effect=[0, 6]), \
                self.assertRaisesRegex(custom.CustomSearchError, "resolved in time"):
            custom.search_custom(website(), "nature", 3)
        future.cancel.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            custom.search_custom(website(), "nature", 3, lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        self.factory.assert_not_called()

    def test_errors_large_pages_and_nonhtml_fail_without_followup(self):
        for body, status, content_type in (("challenge", 403, "text/html"),
                                           (b"x" * (custom.MAX_PAGE_BYTES + 1), 200, "text/html"),
                                           ("binary", 200, "video/mp4")):
            self.session.reset_mock()
            with self.subTest(status=status, type=content_type), self.page(body, status, content_type) as response, \
                    self.assertRaises(custom.CustomSearchError):
                custom.search_custom(website(), "nature", 3)
            self.session.get.assert_called_once()
            response.close.assert_called_once()

    def test_same_site_redirect_is_revalidated_and_final_page_url_resolves_links(self):
        redirect = MagicMock(status_code=302, headers={"Location": "https://www.vimeo.com/results?q=birds"})
        page = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
        page.iter_content.return_value = [b'<a href="12345">Birds</a>']
        self.session.get.side_effect = [redirect, page]
        with self.assertLogs(custom._OUTBOUND_LOG, level="INFO") as logs:
            result = custom.search_custom(website(), "birds", 3)
        self.assertEqual(result["results"][0]["source_url"], "https://vimeo.com/12345")
        self.assertEqual([call.args[0] for call in self.session.get.call_args_list], [
            "https://vimeo.com/?search=birds", "https://www.vimeo.com/results?q=birds"])
        self.assertEqual([call.args[0] for call in self.resolve.call_args_list],
                         ["vimeo.com", "www.vimeo.com"])
        output = "\n".join(logs.output)
        self.assertIn("External search redirect: GET https://vimeo.com/?search=birds -> https://www.vimeo.com/results?q=birds", output)
        self.assertEqual(output.count("External search request:"), 2)
        self.assertIn("GET https://www.vimeo.com/results?q=birds -> HTTP 200", output)
        redirect.close.assert_called_once()
        page.close.assert_called_once()

    def test_same_site_http_redirect_is_upgraded_without_an_insecure_request(self):
        redirect = MagicMock(status_code=302, headers={"Location": "http://www.vimeo.com/results?q=birds"})
        page = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
        page.iter_content.return_value = [b'<a href="/12345">Birds</a>']
        self.session.get.side_effect = [redirect, page]
        result = custom.search_custom(website(), "birds", 3)
        self.assertEqual(result["results"][0]["source_url"], "https://vimeo.com/12345")
        self.assertEqual([call.args[0] for call in self.session.get.call_args_list], [
            "https://vimeo.com/?search=birds", "https://www.vimeo.com/results?q=birds"])

    def test_redirect_cannot_leave_configured_website(self):
        redirect = MagicMock(status_code=302, headers={"Location": "https://evil.example/results"})
        self.session.get.return_value = redirect
        with self.assertRaisesRegex(custom.CustomSearchError, "own website"):
            custom.search_custom(website(), "birds", 3)
        self.session.get.assert_called_once()
        self.resolve.assert_called_once()
        redirect.close.assert_called_once()

    def test_jsonld_and_anchor_candidates_keep_descriptions_filter_domains_and_deduplicate(self):
        payload = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "item": {"@type": "VideoObject", "url": "/123", "name": "<b>Birds</b>",
                                         "description": " Flight\n over water ", "author": {"name": "Creator"}}},
            {"@type": "ListItem", "url": "https://vimeo.com/456", "name": "Water"},
            {"@type": "VideoObject", "url": "https://other.com/789", "name": "Foreign"},
        ]}
        body = '<script type="application/ld+json">' + json.dumps(payload) + '</script>' + """
            <a href="/123">Duplicate</a><a href="/789"><img alt="Sky"></a>
            <a href="/channels/staffpicks">Channel</a><a href="https://vimeo.com.evil.com/888">Wrong domain</a>
            <a href="javascript:alert(1)">Script</a><a href="//127.0.0.1/private">Private</a>
            <script>fetch('https://other.com/private')</script>"""
        with self.page(body):
            result = custom.search_custom(website(), "nature", 8)
        self.assertEqual({item["id"] for item in result["results"]}, {"vimeo.com:123", "vimeo.com:456", "vimeo.com:789"})
        bird = next(item for item in result["results"] if item["id"] == "vimeo.com:123")
        self.assertEqual(bird["title"], "Birds")
        self.assertEqual(bird["description"], "Flight over water")
        self.assertEqual(bird["uploader"], "Creator")
        self.assertTrue(all(item["verified"] is False for item in result["results"]))
        self.session.get.assert_called_once()

    def test_json_response_and_empty_page_have_accurate_status(self):
        with self.page(json.dumps({"@type": "VideoObject", "url": "https://vimeo.com/123", "name": "Nature"}),
                       content_type="application/ld+json"):
            result = custom.search_custom(website(), "nature", 3)
        self.assertEqual(result["status"], "ok")
        with self.page('<html>No matches<script type="application/ld+json">{malformed</script></html>'):
            result = custom.search_custom(website(), "unknown", 3)
        self.assertEqual(result, {"results": [], "warnings": [], "status": "empty", "discovery": "custom_search"})

    def test_fetch_all_returns_every_usable_page_link_including_untitled_links(self):
        body = "".join(f'<a href="/{index}">{"Video " + str(index) if index < 30 else ""}</a>'
                       for index in range(1, 31))
        with self.page(body):
            selected = custom.search_custom(website(), "nature", 24)
        with self.page(body):
            all_links = custom.search_custom(website(), "nature", 200, fetch_all=True)
        self.assertEqual(len(selected["results"]), 24)
        self.assertEqual(len(all_links["results"]), 30)
        self.assertEqual(all_links["results"][-1]["source_url"], "https://vimeo.com/30")
        self.assertEqual(all_links["results"][-1]["title"], "https://vimeo.com/30")

    def test_fetch_all_enriches_each_link_and_keeps_original_metadata_on_failure(self):
        body = '<a href="/1">360pCC</a><a href="/2">Original fallback</a>'

        def metadata(provider, url, guard):
            if url.endswith("/1"):
                return (url, '<meta property="og:title" content="Actual birds title">'
                             '<meta property="og:image" content="/images/birds.jpg">', "text/html")
            raise custom.CustomSearchError("Page unavailable")

        with patch.object(custom, "_search_page", return_value=(
                "https://vimeo.com/search?q=birds", body, "text/html")), \
                patch.object(custom, "fetch_public_page", side_effect=metadata) as fetch:
            result = custom.search_custom(website(), "birds", 200, fetch_all=True)
        self.assertEqual(result["results"][0]["title"], "Actual birds title")
        self.assertEqual(result["results"][0]["thumbnail_url"],
                         "https://vimeo.com/images/birds.jpg")
        self.assertEqual(result["results"][1]["title"], "Original fallback")
        self.assertIsNone(result["results"][1]["thumbnail_url"])
        self.assertEqual(fetch.call_count, 2)

    def test_fetch_all_uses_lazy_search_page_thumbnail_when_video_page_has_none(self):
        provider = RecommendationProvider(
            name="Example videos", domain="videos.example-media.com",
            search_url="https://videos.example-media.com/?k={query}",
            thumbnail_domains=["example-cdn.net"]).model_dump()
        body = ('<a href="/clip/sample-item"><img alt="Bird video" '
                'data-src="https://images-a.example-cdn.net/media/thumb.jpg"></a>')
        with patch.object(custom, "_search_page", return_value=(
                "https://videos.example-media.com/?k=birds", body, "text/html")), \
                patch.object(custom, "fetch_public_page", side_effect=custom.CustomSearchError("No metadata")):
            result = custom.search_custom(provider, "birds", 200, fetch_all=True)
        self.assertEqual(result["results"][0]["thumbnail_url"],
                         "https://images-a.example-cdn.net/media/thumb.jpg")

    def test_later_duplicate_card_fills_missing_structured_thumbnail(self):
        provider = RecommendationProvider(
            name="Example videos", domain="videos.example-media.com",
            search_url="https://videos.example-media.com/?k={query}",
            thumbnail_domains=["example-cdn.net"]).model_dump()
        structured = json.dumps({"@type": "VideoObject", "url": "/clip/sample-item",
                                 "name": "Bird video"})
        body = ('<script type="application/ld+json">' + structured + '</script>'
                '<a href="/clip/sample-item"><img '
                'data-src="https://images-b.example-cdn.net/media/thumb.jpg"></a>')
        with self.page(body):
            result = custom.search_custom(provider, "birds", 1, fetch_all=True)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["thumbnail_url"],
                         "https://images-b.example-cdn.net/media/thumb.jpg")

    def test_fetch_all_surfaces_thumbnail_cards_before_generic_links(self):
        provider = RecommendationProvider(
            name="Example videos", domain="videos.example-media.com",
            search_url="https://videos.example-media.com/?k={query}",
            thumbnail_domains=["example-cdn.net"]).model_dump()
        body = ('<a href="/plain-link">Plain link</a>'
                '<a href="/clip/sample-item"><img alt="Bird video" '
                'data-src="https://images-b.example-cdn.net/media/thumb.jpg"></a>')
        with patch.object(custom, "_search_page", return_value=(
                "https://videos.example-media.com/?k=birds", body, "text/html")), \
                patch.object(custom, "fetch_public_page", side_effect=custom.CustomSearchError("No metadata")):
            result = custom.search_custom(provider, "birds", 200, fetch_all=True)
        self.assertTrue(result["results"][0]["thumbnail_url"])

    def test_thumbnail_domain_detection_suggests_external_https_hosts_without_trusting_them(self):
        provider = website()
        structured = json.dumps({"@type": "VideoObject", "name": "Birds", "url": "/123",
                                 "thumbnailUrl": "https://structured.cdn-assets.net/thumb.jpg"})
        body = ('<meta property="og:image" content="https://images.cdn-example.com/thumb.jpg">'
                '<img data-src="//thumbs.cdn-example.com/one.jpg">'
                '<img src="https://vimeo.com/same-site.jpg">'
                '<img src="http://insecure.example/image.jpg">'
                '<script type="application/ld+json">' + structured + '</script>')
        with patch.object(custom, "_search_page", return_value=(
                "https://vimeo.com/search?q=video", body, "text/html")):
            result = custom.discover_thumbnail_domains(provider)
        self.assertEqual(result, {
            "domains": ["images.cdn-example.com", "thumbs.cdn-example.com", "structured.cdn-assets.net"],
            "page_url": "https://vimeo.com/search?q=video"})


if __name__ == "__main__":
    unittest.main()
