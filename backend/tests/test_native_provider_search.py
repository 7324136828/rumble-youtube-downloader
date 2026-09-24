"""Native video discovery preserves trusted metadata and accurate failure states."""
import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.recommendation_providers import RecommendationProvider
from app.services import native_provider_search as native, recommendation_providers as providers


def website(domain):
    return RecommendationProvider(name=domain, domain=domain).model_dump()


def response(body, status=200):
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    if isinstance(body, str):
        body = body.encode()
    result = MagicMock(status_code=status)
    result.iter_content.return_value = [body]
    return result


class NativeProviderSearchTest(unittest.TestCase):
    def setUp(self):
        providers.clear_search_state()
        self.addCleanup(providers.clear_search_state)

    @contextmanager
    def http(self, *responses):
        with patch.object(native.requests, "Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.get.side_effect = responses
            yield session

    def assert_bounded_requests(self, session, expected_hosts):
        self.assertEqual([urlsplit(call.args[0]).hostname for call in session.get.call_args_list], expected_hosts)
        for call in session.get.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertTrue(call.kwargs["stream"])
            self.assertEqual(call.kwargs["timeout"], native.NATIVE_TIMEOUT)

    def test_vimeo_uses_only_anonymous_page_bootstrap_and_native_video_fields(self):
        bootstrap = response('<script id="viewer-bootstrap" type="application/json">{"user":null,"jwt":"anonymous.public.session"}</script>')
        payload = response({"total": 1, "data": [{"clip": {
            "uri": "/videos/12345", "link": "https://malicious.example.com/video", "name": "Nature &amp; birds",
            "duration": 120, "user": {"name": "Film maker"},
            "pictures": {"sizes": [{"link": "https://i.vimeocdn.com/video/123.jpg"}]},
        }}]})
        with self.http(bootstrap, payload) as session:
            result = native.search_native(website("vimeo.com"), "nature film", 5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["warnings"], [])
        item = result["results"][0]
        self.assertEqual(item["title"], "Nature & birds")
        self.assertEqual(item["source_url"], "https://vimeo.com/12345")
        self.assertEqual(item["uploader"], "Film maker")
        self.assertEqual(item["duration"], 120)
        self.assertEqual(item["thumbnail_url"], "https://i.vimeocdn.com/video/123.jpg")
        self.assertTrue(item["verified"])
        self.assertEqual(item["verification"], "provider_search")
        self.assert_bounded_requests(session, ["vimeo.com", "api.vimeo.com"])
        params = parse_qs(urlsplit(session.get.call_args_list[1].args[0]).query)
        self.assertEqual(params["query"], ["nature film"])
        self.assertEqual(params["filter_type"], ["clip"])
        self.assertEqual(params["per_page"], ["5"])
        self.assertIn("clip.name", params["fields"][0])
        self.assertNotIn("data.clip", params["fields"][0])
        self.assertEqual(session.get.call_args_list[1].kwargs["headers"]["Authorization"], "jwt anonymous.public.session")
        bootstrap.close.assert_called_once()
        payload.close.assert_called_once()

    def test_vimeo_bootstrap_failure_never_sends_token_or_requests_arbitrary_api_host(self):
        for body in ('<html>Login required</html>', '<script id="viewer-bootstrap">{"user":{"id":1},"jwt":"private.user.token"}</script>',
                     '<script id="viewer-bootstrap">{"user":null,"jwt":"bad\\r\\nheader"}</script>'):
            with self.subTest(body=body), self.http(response(body)) as session, self.assertRaises(native.NativeSearchError) as caught:
                native.search_native(website("vimeo.com"), "nature", 3)
            self.assertEqual(session.get.call_count, 1)
            self.assertNotIn("private.user.token", str(caught.exception))

    def test_vimeo_malformed_video_identifiers_fail_as_source_errors(self):
        for uri in (123, {"path": "/videos/1"}, [], None):
            payload = {"total": 1, "data": [{"clip": {"uri": uri, "name": "Malformed video"}}]}
            with self.subTest(uri=uri), self.http(
                    response('<script id="viewer-bootstrap">{"user":null,"jwt":"anonymous.public.session"}</script>'),
                    response(payload)), self.assertRaises(native.NativeSearchError):
                native.search_native(website("vimeo.com"), "nature", 3)
        with self.http(response('<script id="viewer-bootstrap">{"user":null,"jwt":"anonymous.public.session"}</script>'),
                       response({"total": 1, "data": [{"clip": {"uri": "/videos/123", "name": "Nature", "duration": 10 ** 400}}]})):
            result = native.search_native(website("vimeo.com"), "nature", 3)
        self.assertIsNone(result["results"][0]["duration"])

    def test_bilibili_tv_uses_video_modules_and_ignores_series_and_creators(self):
        payload = {"code": 0, "data": {"modules": [
            {"type": "ogv", "items": [{"season_id": "999", "title": "Series"}]},
            {"type": "creator", "items": [{"mid": "123", "title": "Creator"}]},
            {"type": "recommend", "items": [{"aid": "888", "title": "Unrelated recommendation"}]},
            {"type": "ugc", "items": [
                {"aid": "2043775763", "title": "Nature", "duration": "1:02:03", "author": {"nickname": "Creator"},
                 "cover": "https://pic.bstarstatic.com/ugc/1.jpg"},
                {"aid": "2043775763", "title": "Duplicate"}, {"aid": "invalid", "title": "Invalid ID"},
            ]},
        ]}}
        with self.http(response(payload)) as session:
            result = native.search_native(website("bilibili.tv"), "nature", 5)
        self.assertEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertEqual(item["id"], "bilibili.tv:2043775763")
        self.assertEqual(item["source_url"], "https://bilibili.tv/en/video/2043775763")
        self.assertEqual(item["duration"], 3723)
        self.assertEqual(item["uploader"], "Creator")
        self.assertTrue(item["verified"])
        self.assert_bounded_requests(session, ["api.bilibili.tv"])
        url = urlsplit(session.get.call_args.args[0])
        self.assertEqual(url.path, "/intl/gateway/web/v2/search_v2")
        params = parse_qs(url.query, keep_blank_values=True)
        for field in ("keyword", "highlight", "pn", "ps", "qid", "sort", "duration_type"):
            self.assertIn(field, params)
        self.assertIsNone(session.get.call_args.kwargs["cookies"])

    def test_bilibili_com_preserves_metadata_and_generates_only_anonymous_visitor_cookie(self):
        payload = {"code": 0, "data": {"numResults": 1, "result": [
            {"type": "video", "bvid": "BV1Wahh66EoH", "title": '<em class="keyword">Nature</em> &amp; birds',
             "arcurl": "http://127.0.0.1/private", "author": "Director", "duration": "29:10",
             "pic": "//i2.hdslb.com/bfs/archive/1.jpg"},
        ]}}
        with self.http(response(payload)) as session:
            result = native.search_native(website("bilibili.com"), "nature", 3)
        item = result["results"][0]
        self.assertEqual(item["source_url"], "https://bilibili.com/video/BV1Wahh66EoH")
        self.assertEqual(item["title"], "Nature & birds")
        self.assertEqual(item["duration"], 1750)
        self.assertEqual(item["thumbnail_url"], "https://i2.hdslb.com/bfs/archive/1.jpg")
        self.assertTrue(item["verified"])
        self.assert_bounded_requests(session, ["api.bilibili.com"])
        cookies = session.get.call_args.kwargs["cookies"]
        self.assertEqual(set(cookies), {"buvid3"})
        self.assertRegex(cookies["buvid3"], r"^[0-9a-f-]{36}infoc$")
        self.assertEqual(parse_qs(urlsplit(session.get.call_args.args[0]).query)["keyword"], ["nature"])

    def test_native_empty_responses_are_authoritative_and_silent(self):
        cases = [("bilibili.tv", {"code": 0, "data": {"modules": []}}),
                 ("bilibili.com", {"code": 0, "data": {"numResults": 0, "result": None}})]
        for domain, payload in cases:
            with self.subTest(domain=domain), self.http(response(payload)):
                result = native.search_native(website(domain), "unknown", 3)
            self.assertEqual(result["results"], [])
            self.assertEqual(result["status"], "empty")
            self.assertEqual(result["warnings"], [])
        with self.http(response('<script id="viewer-bootstrap">{"user":null,"jwt":"anonymous.public.session"}</script>'),
                       response({"total": 0, "data": []})):
            result = native.search_native(website("vimeo.com"), "unknown", 3)
        self.assertEqual(result["status"], "empty")

    def test_failure_and_malformed_payload_are_not_reported_as_no_results(self):
        for payload in ({"code": -352, "message": "secret-cookie upstream details"},
                        {"code": 0, "data": {}}, {"code": 0, "data": {"modules": {}}},
                        {"code": 0, "data": {"modules": [{"type": "ugc", "items": [{"aid": "bad", "title": "x"}]}]}}):
            with self.subTest(payload=payload), self.http(response(payload)), self.assertRaises(native.NativeSearchError) as caught:
                native.search_native(website("bilibili.tv"), "nature", 3)
            self.assertNotIn("secret-cookie", str(caught.exception))
        for reply in (response("redirect", 302), response("rate limited", 429), response("<html>captcha</html>"),
                      response(b"x" * (native.MAX_RESPONSE_BYTES + 1))):
            with self.http(reply), self.assertRaises(native.NativeSearchError):
                native.search_native(website("bilibili.tv"), "nature", 3)
            reply.close.assert_called_once()

    def test_unsupported_domains_never_receive_native_requests(self):
        with patch.object(native.requests, "Session") as session:
            for domain in ("instagram.com", "customvideo.com", "api.bilibili.com"):
                self.assertFalse(native.supports_native_search(domain))
                with self.assertRaises(native.NativeSearchError):
                    native.search_native(website(domain), "nature", 3)
        session.assert_not_called()

    def test_native_success_empty_and_failure_are_authoritative(self):
        provider = website("vimeo.com")
        video = {"id": "vimeo.com:1", "source_url": "https://vimeo.com/1", "title": "Nature", "verified": True, "verification": "provider_search"}
        for results in ([video], []):
            providers.clear_search_state()
            result = {"results": results, "warnings": [], "status": "ok" if results else "empty", "discovery": "provider_search"}
            with patch.object(native, "search_native", return_value=result):
                combined = providers.search_website(provider, "nature", 3)
            self.assertEqual(len(combined["results"]), len(results))
            if results:
                self.assertTrue(combined["results"][0]["verified"])
                self.assertEqual(combined["results"][0]["origins"], ["custom_search"])
        providers.clear_search_state()
        with patch.object(native, "search_native", side_effect=native.NativeSearchError("Vimeo's own search is unavailable (HTTP 429).")):
            result = providers.search_website(provider, "nature", 3)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("HTTP 429", result["warnings"][0])

    def test_generic_domains_require_a_configured_search_url(self):
        with patch.object(native, "search_native") as adapter:
            returned = providers.search_website(website("instagram.com"), "nature", 3)
        adapter.assert_not_called()
        self.assertEqual(returned["results"], [])
        self.assertEqual(returned["status"], "unavailable")
        self.assertIn("configured search URL", returned["warnings"][0])


if __name__ == "__main__":
    unittest.main()
