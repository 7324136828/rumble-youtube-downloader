"""Offline Connector, tool-loop, validation, caching, and opt-out regressions."""
import copy
import json
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.recommendations import router
from app.schemas.recommendations import RecommendationRequest
from app.schemas.search import SearchResponse, SearchResult
from app.services import connector_client, db, recommendation_tools as tools, recommendations as rec


def video(source="youtube", ident="abcdefghijk", title="Bird watching"):
    url = f"https://www.youtube.com/watch?v={ident}" if source == "youtube" else f"https://rumble.com/{ident}-nature.html"
    return SearchResult(id=f"{source}:{ident}", title=title, connector=source, source_url=url).model_dump()


def assistant(content):
    return {"role": "assistant", "content": json.dumps(content)}


class RecommendationTest(unittest.TestCase):
    def setUp(self):
        self.settings = {"enabled": True, "model_id": "nature-config", "seed_keywords": ["birds"], "revision": 1}
        self.history = []
        self.local_videos = []
        self.patches = [
            patch.object(config, "RECOMMENDATION_CONNECTOR_URL", "http://127.0.0.1:8301", create=True),
            patch.object(db, "get_recommendation_settings", side_effect=lambda: copy.deepcopy(self.settings), create=True),
            patch.object(db, "update_recommendation_settings", side_effect=self.update_settings, create=True),
            patch.object(db, "list_watch_history", side_effect=lambda limit=30: copy.deepcopy(self.history[:limit]), create=True),
            patch.object(db, "list_videos", side_effect=lambda status=None: copy.deepcopy(self.local_videos)),
            patch.object(db, "get_video", return_value=None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        rec._CACHE.clear()
        rec._INFLIGHT.clear()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def update_settings(self, changes, **kwargs):
        if kwargs.get("expected_revision", self.settings["revision"]) != self.settings["revision"]:
            raise db.SettingsConflictError("Settings changed")
        self.settings.update(changes)
        self.settings["revision"] += 1
        return copy.deepcopy(self.settings)

    def complete_and_search(self, items=None, playlist=None):
        items = items or [video(), video("rumble", "v123abc")]
        playlist = playlist or [{"id": item["id"], "reason": "Related topic"} for item in items]
        return (patch.object(connector_client, "complete", side_effect=[assistant({"keywords": ["birds"]}), assistant({"playlist": playlist})]),
                patch.object(tools, "search_by_key_words", return_value={"results": items, "warnings": []}))

    def test_disabled_and_cold_start_do_not_infer(self):
        with patch.object(connector_client, "complete") as completion:
            self.settings["enabled"] = False
            self.assertEqual(self.client.post("/api/recommendations", json={}).json()["status"], "disabled")
            self.settings.update(enabled=True, seed_keywords=[])
            self.assertEqual(self.client.post("/api/recommendations", json={}).json()["status"], "needs_history")
        completion.assert_not_called()

    def test_disabling_does_not_require_connector(self):
        with patch.object(connector_client, "discover_models", side_effect=AssertionError("No network")):
            response = self.client.patch("/api/recommendations/settings", json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["enabled"])
        self.assertEqual(response.json()["revision"], 2)

    def test_selection_requires_discovered_active_model(self):
        with patch.object(connector_client, "discover_models", return_value={"models": [{"id": "valid"}]}):
            bad = self.client.patch("/api/recommendations/settings", json={"model_id": "raw-provider-model"})
            good = self.client.patch("/api/recommendations/settings", json={"model_id": "valid"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(good.json()["model_id"], "valid")

    def test_slow_selection_does_not_override_disable(self):
        def discover():
            self.update_settings({"enabled": False})
            return {"models": [{"id": "valid"}]}
        with patch.object(connector_client, "discover_models", side_effect=discover):
            response = self.client.patch("/api/recommendations/settings", json={"model_id": "valid", "enabled": True})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.settings["enabled"])

    def test_settings_input_bounds(self):
        for payload in ({"seed_keywords": ["a"] * 7}, {"seed_keywords": [" "]}, {"enabled": None},
                        {"enabled": "true"}, {"seed_keywords": None}, {"connector_url": "https://evil.test"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.patch("/api/recommendations/settings", json=payload).status_code, 422)

    def test_seed_topics_generate_verified_playlist_and_ready_link(self):
        self.local_videos = [{**video(), "id": "local-id", "status": "ready"}]
        completions, searches = self.complete_and_search()
        with completions as completion, searches:
            response = self.client.post("/api/recommendations", json={"context": "feed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["media_id"], "local-id")
        self.assertEqual(response.json()["keywords"], ["birds"])
        self.assertEqual(completion.call_args_list[0].args[0], "nature-config")
        self.assertIn('"seed_keywords": ["birds"]', completion.call_args_list[0].args[1][1]["content"])

    def test_hallucinations_duplicates_watched_and_exclusions_removed(self):
        watched = video(ident="AAAAAAAAAAA")
        excluded = video(ident="BBBBBBBBBBB")
        current = video(ident="CCCCCCCCCCC")
        self.history = [watched]
        completions, searches = self.complete_and_search(
            [watched, excluded, current, video()],
            [{"id": "youtube:invented000"}, {"id": watched["id"]}, {"id": video()["id"]}, {"id": video()["id"]}])
        with completions, searches, patch.object(db, "get_video", return_value=current):
            response = self.client.post("/api/recommendations", json={"context": "watch", "video_id": "local-current",
                "exclude_urls": [excluded["source_url"]]})
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["items"]], [video()["id"]])
        self.assertTrue(any("unverified" in warning for warning in payload["warnings"]))

    def test_full_assistant_message_and_tool_result_are_replayed(self):
        tool_message = {"role": "assistant", "content": None,
            "provider_specific_fields": {"signature": "opaque-native-replay"},
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "search_by_url",
                "arguments": json.dumps({"url": video()["source_url"]})}}]}
        replies = [tool_message, assistant({"keywords": ["birds"]}), assistant({"playlist": [video()["id"]]})]
        requests = []
        def complete(model, messages, tool_schemas=None, **kwargs):
            requests.append(copy.deepcopy(messages))
            return replies.pop(0)
        with patch.object(connector_client, "complete", side_effect=complete), \
             patch.object(tools, "search_by_url", return_value=video()), \
             patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(requests[1][2], tool_message)
        self.assertEqual(requests[1][3]["tool_call_id"], "call-1")
        self.assertEqual(json.loads(requests[1][3]["content"])["id"], video()["id"])

    def test_unknown_tool_and_bad_arguments_are_returned_to_model_safely(self):
        for name, arguments in (("execute_python", "{}"), ("search_by_url", "not json")):
            replies = [{"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function",
                       "function": {"name": name, "arguments": arguments}}]}, assistant({"keywords": ["birds"]}),
                       assistant({"playlist": [video()["id"]]})]
            requests = []
            def complete(model, messages, tool_schemas=None, **kwargs):
                requests.append(copy.deepcopy(messages))
                return replies.pop(0)
            with self.subTest(name=name), patch.object(connector_client, "complete", side_effect=complete), \
                 patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}):
                rec._CACHE.clear()
                response = self.client.post("/api/recommendations", json={})
                self.assertEqual(response.status_code, 200)
                self.assertIn("error", json.loads(requests[1][3]["content"]))

    def test_invalid_json_has_bounded_retries(self):
        with patch.object(connector_client, "complete", return_value={"role": "assistant", "content": "not json"}) as completion, \
             patch.object(tools, "search_by_key_words") as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(completion.call_count, 4)
        search.assert_not_called()

    def test_markdown_wrapped_keyword_and_playlist_json_are_accepted(self):
        replies = [
            {"role": "assistant", "content": '```json\n{"keywords":["birds"]}\n```'},
            {"role": "assistant", "content": '```\n' + json.dumps({"playlist": [video()["id"]]}) + '\n```'},
        ]
        with patch.object(connector_client, "complete", side_effect=replies) as completion, \
                patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], video()["id"])
        self.assertEqual(completion.call_count, 2)

    def test_invalid_playlist_retries_once_and_keeps_verified_candidates(self):
        malformed = {"role": "assistant", "content": 'Here is your playlist:',
                     "provider_specific_fields": {"signature": "native-replay"}}
        requests = []
        replies = [assistant({"keywords": ["birds"]}), malformed,
                   assistant({"playlist": [video()["id"], "youtube:invented000"]})]
        def complete(model, messages, tools=None, **kwargs):
            requests.append((copy.deepcopy(messages), kwargs))
            return replies.pop(0)
        with patch.object(connector_client, "complete", side_effect=complete), \
                patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}) as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["items"]], [video()["id"]])
        self.assertIn(malformed, requests[2][0])
        self.assertEqual(requests[2][1]["max_tokens"], 4096)
        self.assertEqual(len(requests), 3)
        search.assert_called_once()

    def test_truncated_playlist_has_one_retry_with_larger_output_budget(self):
        replies = [assistant({"keywords": ["birds"]}), connector_client.CompletionTruncatedError("Output truncated"),
                   assistant({"playlist": [video()["id"]]})]
        with patch.object(connector_client, "complete", side_effect=replies) as completion, \
                patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(completion.call_count, 3)
        self.assertEqual(completion.call_args.kwargs["max_tokens"], 4096)

    def test_repeated_bad_playlist_has_actionable_sanitized_error_and_no_more_retries(self):
        replies = [assistant({"keywords": ["birds"]}), assistant({"youtube": [video()["source_url"]]}),
                   {"role": "assistant", "content": "private model text"}]
        with patch.object(connector_client, "complete", side_effect=replies) as completion, \
                patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}), \
                self.assertLogs("app.routers.recommendations", level="WARNING") as logged:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 502)
        self.assertIn("JSON playlist after one retry", response.json()["detail"])
        self.assertIn(response.json()["detail"], logged.output[0])
        self.assertNotIn("private model text", " ".join(logged.output))
        self.assertEqual(completion.call_count, 3)

    def test_disabling_during_bad_playlist_prevents_repair_call(self):
        replies = [assistant({"keywords": ["birds"]}), {"role": "assistant", "content": "not JSON"}]
        def complete(*args, **kwargs):
            if len(replies) == 1:
                self.update_settings({"enabled": False})
            return replies.pop(0)
        with patch.object(connector_client, "complete", side_effect=complete) as completion, \
                patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.json()["status"], "disabled")
        self.assertEqual(completion.call_count, 2)

    def test_disable_during_completion_stops_tools_and_results(self):
        def complete(*args, **kwargs):
            self.update_settings({"enabled": False})
            return assistant({"keywords": ["birds"]})
        with patch.object(connector_client, "complete", side_effect=complete), patch.object(tools, "search_by_key_words") as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.json()["status"], "disabled")
        search.assert_not_called()

    def test_disable_during_failed_completion_also_discards_error(self):
        def complete(*args, **kwargs):
            self.update_settings({"enabled": False})
            raise connector_client.ConnectorError("Provider error")
        with patch.object(connector_client, "complete", side_effect=complete):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.json()["status"], "disabled")

    def test_revision_change_during_search_stops_ranking(self):
        def search(*args, **kwargs):
            self.update_settings({"seed_keywords": ["music"]})
            return {"results": [video()], "warnings": []}
        with patch.object(connector_client, "complete", return_value=assistant({"keywords": ["birds"]})) as completion, \
             patch.object(tools, "search_by_key_words", side_effect=search):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.json()["status"], "disabled")
        self.assertEqual(completion.call_count, 1)

    def test_cache_reuses_then_settings_invalidate(self):
        completions, searches = self.complete_and_search()
        with completions as completion, searches:
            first = self.client.post("/api/recommendations", json={}).json()
            second = self.client.post("/api/recommendations", json={}).json()
        self.assertEqual(first, second)
        self.assertEqual(completion.call_count, 2)
        self.client.patch("/api/recommendations/settings", json={"seed_keywords": ["piano"]})
        completions, searches = self.complete_and_search()
        with completions as completion, searches:
            self.client.post("/api/recommendations", json={})
        self.assertEqual(completion.call_count, 2)

    def test_refresh_bypasses_cache(self):
        completions, searches = self.complete_and_search()
        with completions, searches:
            self.client.post("/api/recommendations", json={})
        completions, searches = self.complete_and_search()
        with completions as completion, searches:
            self.client.post("/api/recommendations", json={"refresh": True})
        self.assertEqual(completion.call_count, 2)

    def test_identical_inflight_requests_share_work(self):
        entered, release = threading.Event(), threading.Event()
        def generate(*args):
            entered.set()
            release.wait(2)
            return {"status": "ready", "items": [], "keywords": ["birds"], "warnings": []}
        with patch.object(rec, "_generate", side_effect=generate) as generation, ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(rec.recommend, RecommendationRequest())
            self.assertTrue(entered.wait(2))
            second = executor.submit(rec.recommend, RecommendationRequest())
            release.set()
            self.assertEqual(first.result(), second.result())
        self.assertEqual(generation.call_count, 1)


class ConnectorTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(config, "RECOMMENDATION_CONNECTOR_URL", "http://connector.test", create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def transport(self, handler):
        client_type = httpx.Client
        return patch.object(connector_client.httpx, "Client", side_effect=lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))

    def test_discovery_uses_saved_models(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"object": "list", "data": [{"id": "saved-model", "name": "My routing"},
                {"id": "invalid/id"}, {"id": "saved-model"}]})
        with self.transport(handler):
            models = connector_client.discover_models()
        self.assertEqual(requests[0].url.path, "/v1/models")
        self.assertEqual(models["models"], [{"id": "saved-model", "name": "My routing", "description": ""}])

    def test_import_validates_raw_then_persists_normalized_config(self):
        requests = []
        raw = {"sequences": [{"provider": "mock", "model": "mock-assistant"}]}
        normalized = {**raw, "past_memory": False}
        def handler(request):
            body = json.loads(request.content)
            requests.append((request.url.path, body))
            return httpx.Response(200, json=normalized if request.url.path.endswith("validate") else body)
        with self.transport(handler):
            result = connector_client.import_config("My config", raw)
        self.assertEqual(requests[0], ("/api/config/validate", raw))
        self.assertEqual(requests[1][0], "/api/configs")
        self.assertEqual(requests[1][1]["config"], normalized)
        self.assertTrue(requests[1][1]["active"])
        self.assertEqual(result["model_id"], requests[1][1]["model_id"])

    def test_completion_portability_and_native_metadata(self):
        sent = []
        message = {"role": "assistant", "content": "{}", "provider_specific_fields": {"signature": "native"}}
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": message}]})
        with self.transport(handler):
            response = connector_client.complete("saved-config", [{"role": "user", "content": "hi"}], tools.TOOLS)
        self.assertEqual(response, message)
        self.assertEqual(sent[0]["tool_choice"], "auto")
        self.assertNotIn("response_format", sent[0])
        self.assertNotIn("temperature", sent[0])

    def test_connector_error_does_not_expose_upstream_secrets(self):
        with self.transport(lambda request: httpx.Response(502, json={"error": "secret-token private path"})):
            with self.assertRaises(connector_client.ConnectorError) as caught:
                connector_client.discover_models()
        self.assertNotIn("secret", str(caught.exception))


class SearchToolTest(unittest.TestCase):
    def test_url_allowlist_normalizes_individual_videos(self):
        self.assertEqual(tools.canonical_video_url("youtube.com/watch?v=abcdefghijk")[1], video()["source_url"])
        self.assertEqual(tools.canonical_video_url("https://youtu.be/abcdefghijk?t=4")[1], video()["source_url"])
        self.assertEqual(tools.canonical_video_url("https://www.youtube.com/shorts/abcdefghijk")[2], "youtube:abcdefghijk")
        for bad in ("http://127.0.0.1/private", "https://youtube.com.evil.test/watch?v=abcdefghijk", "file:///secret",
                    "https://www.youtube.com/playlist?list=123", "https://user:pass@rumble.com/v123abc.html",
                    "https://rumble.com/c/channel", "https://www.youtube.com:123/watch?v=abcdefghijk"):
            self.assertIsNone(tools.canonical_video_url(bad))

    def test_metadata_only_url_tool(self):
        data = {"id": "abcdefghijk", "title": "Birds", "duration": 30, "uploader": "Wildlife"}
        with patch.object(tools.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")) as run:
            result = tools.search_by_url("https://youtu.be/abcdefghijk")
        self.assertEqual(result["title"], "Birds")
        command = run.call_args.args[0]
        self.assertIn("--skip-download", command)
        self.assertIn("--no-playlist", command)
        self.assertIn("--ignore-config", command)
        self.assertEqual(command[-1], video()["source_url"])

    def test_rumble_metadata_uses_browser_impersonation(self):
        data = {"id": "12345", "title": "Nature", "duration": 30}
        with patch.object(tools.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")) as run:
            result = tools.search_by_url("rumble.com/v123abc-nature.html")
        self.assertEqual(result["id"], "rumble:v123abc")
        self.assertIn("--impersonate", run.call_args.args[0])

    def test_invalid_url_never_starts_extractor(self):
        with patch.object(tools.subprocess, "run") as run:
            with self.assertRaises(tools.ToolError):
                tools.search_by_url("http://localhost/secret")
        run.assert_not_called()

    def test_keyword_results_round_robin_sources_and_deduplicate(self):
        batches = {"birds": [video(), video("rumble", "v123abc"), video(ident="AAAAAAAAAAA")],
                   "nature": [video(), video("rumble", "v456def"), video(ident="BBBBBBBBBBB")]}
        def search(keyword, source, limit):
            return SearchResponse(query=keyword, source=source, results=[SearchResult(**item) for item in batches[keyword]], warnings=[])
        with patch.object(tools.search, "search_videos", side_effect=search):
            result = tools.search_by_key_words(["birds", "nature"], 5)
        self.assertEqual([item["connector"] for item in result["results"]], ["youtube", "rumble", "youtube", "rumble", "youtube"])
        self.assertEqual(len({item["id"] for item in result["results"]}), 5)

    def test_keyword_input_bounds_prevent_work(self):
        with patch.object(tools.search, "search_videos") as search:
            for keywords, limit in (([], 3), (["birds"] * 7, 3), ([" "], 3), (["birds"], True), (["birds"], 25)):
                with self.assertRaises(tools.ToolError):
                    tools.search_by_key_words(keywords, limit)
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
