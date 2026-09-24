"""Offline first-party website discovery and URL-boundary regressions."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from app.schemas.recommendation_providers import RecommendationProvider, default_providers, validate_provider_list
from app.schemas.search import SearchResponse, SearchResult
from app.services import db, recommendation_providers as providers, recommendation_tools as tools, search


def website(domain, name=None, enabled=True, search_url=None):
    values = {"domain": domain, "name": name or domain, "enabled": enabled}
    if search_url is not None:
        values["search_url"] = search_url
    return RecommendationProvider(**values).model_dump()


def discovered_video(domain="vimeo.com", ident="123456", title="Nature"):
    normalized = tools.canonical_video_url(f"https://{domain}/{ident}", [website(domain)])
    return {"id": normalized[2], "connector": normalized[0], "source_url": normalized[1],
            "title": title, "verified": False, "verification": "custom_search"}


class RecommendationProviderTest(unittest.TestCase):
    def setUp(self):
        providers.clear_search_state()
        self.addCleanup(providers.clear_search_state)
        patcher = patch.object(db, "get_recommendation_settings", return_value={"providers": default_providers()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_domains_and_ids_are_normalized_without_mutable_shared_defaults(self):
        self.assertEqual(website("https://WWW.Vimeo.com/", " Vimeo  videos "),
                         {"id": "vimeo.com", "name": "Vimeo videos", "domain": "vimeo.com", "enabled": True})
        self.assertEqual(website("www.youtube.com")["id"], "youtube")
        one = default_providers()
        one[0]["enabled"] = False
        self.assertTrue(default_providers()[0]["enabled"])
        with self.assertRaises(ValueError):
            validate_provider_list([RecommendationProvider(**website("vimeo.com"))] * 2)
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate_provider_list([RecommendationProvider(**website("vimeo.com")),
                                    RecommendationProvider(**website("player.vimeo.com"))])

    def test_private_nonwebsite_and_conflicting_provider_configuration_rejected(self):
        for domain in ("localhost", "127.0.0.1", "[::1]", "10.0.0.1", "videos.local", "test.internal",
                       "file:///secret", "https://user:pass@vimeo.com", "https://vimeo.com:8000",
                       "vimeo.com/123", "vimeo.com?x=1", "vimeo.com#video", "https://vimeo.com\\@evil.com"):
            with self.subTest(domain=domain), self.assertRaises(ValidationError):
                website(domain)
        with self.assertRaises(ValidationError):
            RecommendationProvider(id="youtube", name="Other", domain="vimeo.com")
        with self.assertRaises(ValidationError):
            RecommendationProvider(name=" ", domain="vimeo.com")

    def test_search_templates_are_same_website_https_and_have_one_query(self):
        for raw, normalized in (("vimeo.com/?search=", "https://vimeo.com/?search={query}"),
                                ("https://www.vimeo.com:443/search/{query}?sort=new", "https://www.vimeo.com/search/{query}?sort=new"),
                                ("https://search.vimeo.com/?q={query}", "https://search.vimeo.com/?q={query}")):
            with self.subTest(raw=raw):
                value = RecommendationProvider(name="Vimeo", domain="vimeo.com", search_url=raw)
                self.assertEqual(value.search_url, normalized)
                self.assertEqual(value.model_dump()["search_url"], normalized)
        for raw in (None, "", "   "):
            self.assertNotIn("search_url", RecommendationProvider(name="Vimeo", domain="vimeo.com", search_url=raw).model_dump())
        for raw in ("http://vimeo.com/?q={query}", "https://vimeo.com.evil.com/?q={query}",
                    "https://user:pass@vimeo.com/?q={query}", "https://vimeo.com:8443/?q={query}",
                    "https://vimeo.com/?q={query}#", "https://vimeo.com/?q={query}%0aHeader",
                    "https://127.0.0.1/?q={query}", "https://localhost/?q={query}",
                    "https://{query}.vimeo.com/search", "https://vimeo.com/{query}?q={query}",
                    "https://vimeo.com/?q={other}", "https://vimeo.com/search", "https://vimeo.com/%5c?q={query}",
                    "\nhttps://vimeo.com/?q={query}", "https://vimeo.com/?q={query}\t",
                    "https://vimeo.com/?" + "q" * 2025 + "=", "vimeo.com/?" + "q" * 2025 + "={query}"):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                RecommendationProvider(name="Vimeo", domain="vimeo.com", search_url=raw)

    def test_thumbnail_cdn_domains_are_normalized_bounded_and_optional(self):
        provider = RecommendationProvider(
            name="Vimeo", domain="vimeo.com",
            thumbnail_domains=["https://IMG.VimeoCDN.com/", "vimeocdn.com"])
        self.assertEqual(provider.model_dump()["thumbnail_domains"],
                         ["img.vimeocdn.com", "vimeocdn.com"])
        self.assertNotIn("thumbnail_domains", website("vimeo.com"))
        for domains in (["localhost"], ["cdn.example.com", "cdn.example.com"],
                        [f"cdn{index}.com" for index in range(9)]):
            with self.subTest(domains=domains), self.assertRaises(ValidationError):
                RecommendationProvider(name="Vimeo", domain="vimeo.com",
                                       thumbnail_domains=domains)

    def test_known_custom_individual_urls_and_disabled_provider_filter(self):
        configured = [website("vimeo.com"), website("bilibili.tv"), website("instagram.com")]
        cases = {
            "https://www.vimeo.com/12345?utm_source=feed": ("vimeo.com", "https://vimeo.com/12345"),
            "https://player.vimeo.com/video/12345?h=abc": ("vimeo.com", "https://vimeo.com/12345?h=abc"),
            "https://www.bilibili.tv/en/video/2044418001": ("bilibili.tv", "https://bilibili.tv/en/video/2044418001"),
            "instagram.com/reel/Abc_123/?igsh=xyz": ("instagram.com", "https://instagram.com/reel/Abc_123"),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(tools.canonical_video_url(url, configured)[:2], expected)
        for url in ("https://vimeo.com/channels/staffpicks", "https://instagram.com/someuser",
                    "https://instagram.com/explore", "https://bilibili.tv/en/search?keyword=birds",
                    "https://vimeo.com.evil.com/12345", "https://vimeo.com@evil.com/12345",
                    "https://vimeo.com:8000/12345", "file://vimeo.com/12345"):
            with self.subTest(url=url):
                self.assertIsNone(tools.canonical_video_url(url, configured))
        self.assertIsNone(tools.canonical_video_url("https://vimeo.com/12345", [website("vimeo.com", enabled=False)]))
        self.assertIsNone(tools.canonical_video_url("https://youtu.be/abcdefghijk", configured))

    def test_generic_individual_paths_reject_navigation(self):
        configured = [website("dailymotion.com")]
        self.assertIsNotNone(tools.canonical_video_url("https://dailymotion.com/video/x1234?utm_campaign=test", configured))
        for path in ("/", "/search/birds", "/user/somebody", "/@someone", "/watch", "/file.pdf", "/foo/../bar"):
            self.assertIsNone(tools.canonical_video_url("https://dailymotion.com" + path, configured))
        for path in ("/video/foo%3Fbar", "/video/foo%23bar", "/video/foo%2Fbar"):
            url = "https://dailymotion.com" + path
            normalized = tools.canonical_video_url(url, configured)
            self.assertEqual(normalized[1], url)
            self.assertEqual(normalized, tools.canonical_video_url(normalized[1], configured))

    def test_native_search_is_cached_without_external_fallback(self):
        provider = website("vimeo.com")
        items = [{**discovered_video(ident=str(index + 1)), "verified": True,
                  "verification": "provider_search"} for index in range(24)]
        response = {"results": items, "warnings": [], "status": "ok", "discovery": "provider_search"}
        with patch.object(providers.native_provider_search, "search_native", return_value=response) as native:
            first = providers.search_website(provider, " Nature ", 3)
            second = providers.search_website(provider, "Nature", 8)
        self.assertEqual([len(first["results"]), len(second["results"])], [3, 8])
        self.assertTrue(all(item["verified"] for item in second["results"]))
        native.assert_called_once()
        self.assertEqual(native.call_args.args[1:3], ("Nature", 24))

    def test_configured_search_failure_has_no_external_fallback(self):
        provider = website("vimeo.com", search_url="https://vimeo.com/search?q={query}")
        with patch.object(providers.custom_website_search, "search_custom",
                          side_effect=providers.custom_website_search.CustomSearchError("Configured search failed.")) as own:
            result = providers.search_website(provider, "nature", 3)
        own.assert_called_once()
        self.assertEqual(result["results"], [])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["warnings"], ["Configured search failed."])

    def test_provider_without_search_method_returns_configuration_warning(self):
        with patch.object(providers.native_provider_search, "search_native") as native, \
                patch.object(providers.custom_website_search, "search_custom") as custom:
            result = providers.search_website(website("instagram.com", "Instagram"), "nature", 3)
        native.assert_not_called()
        custom.assert_not_called()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("configured search URL", result["warnings"][0])

    def test_custom_url_metadata_failure_does_not_start_an_external_search(self):
        candidate = discovered_video()
        with patch.object(tools.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1, "", "")) as extractor, \
                self.assertRaisesRegex(tools.ToolError, "configured website"):
            tools.search_by_url(candidate["source_url"], providers=[website("vimeo.com")])
        extractor.assert_called_once()

    def test_dynamic_search_interleaves_and_preserves_verification(self):
        native = SearchResult(id="youtube:abcdefghijk", title="Birds", connector="youtube",
                              source_url="https://www.youtube.com/watch?v=abcdefghijk")
        configured = [default_providers()[0], website("vimeo.com")]
        with patch.object(search, "search_videos", return_value=SearchResponse(query="birds", source="youtube", results=[native], warnings=[])) as builtin, \
                patch.object(providers, "search_website", return_value={"results": [discovered_video()], "warnings": []}) as website_search:
            result = tools.search_by_key_words(["birds", "nature"], 6, providers=configured)
        self.assertEqual([item["connector"] for item in result["results"]], ["youtube", "vimeo.com"])
        self.assertEqual([item["verified"] for item in result["results"]], [True, False])
        self.assertEqual(builtin.call_count, 2)
        self.assertTrue(all(call.args[1] == "youtube" for call in builtin.call_args_list))
        website_search.assert_called_once()

    def test_selected_custom_provider_avoids_native_search_and_disabled_work(self):
        configured = default_providers() + [website("vimeo.com")]
        with patch.object(search, "search_videos") as builtin, \
                patch.object(providers, "search_website", return_value={"results": [discovered_video()], "warnings": []}) as website_search:
            result = tools.dispatch("search_by_key_words", {"keywords": ["birds"]}, providers=configured, source="vimeo.com")
            self.assertEqual(result["results"][0]["connector"], "vimeo.com")
            with self.assertRaises(tools.ToolError):
                tools.search_by_key_words(["birds"], source="instagram.com", providers=configured)
            self.assertEqual(tools.search_by_key_words(["birds"], providers=[])["results"], [])
        builtin.assert_not_called()
        website_search.assert_called_once()
        with patch.object(tools, "search_by_url") as metadata, self.assertRaises(tools.ToolError):
            tools.dispatch("search_by_url", {"url": "https://vimeo.com/12345"}, providers=configured, source="youtube")
        metadata.assert_not_called()


if __name__ == "__main__":
    unittest.main()
