"""Offline Connector, tool-loop, validation, caching, and opt-out regressions."""
import copy
import json
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.recommendations import router
from app.schemas.recommendation_providers import default_providers
from app.schemas.recommendations import RecommendationRequest
from app.schemas.search import SearchResponse, SearchResult
from app.services import connector_client, db, recommendation_providers, recommendation_tools as tools, recommendations as rec


def video(source="youtube", ident="abcdefghijk", title="Bird watching"):
    url = f"https://www.youtube.com/watch?v={ident}" if source == "youtube" else f"https://rumble.com/{ident}-nature.html"
    return {**SearchResult(id=f"{source}:{ident}", title=title, connector=source, source_url=url).model_dump(),
            "verified": True}


def assistant(content):
    return {"role": "assistant", "content": json.dumps(content)}


class RecommendationTest(unittest.TestCase):
    def setUp(self):
        recommendation_providers.clear_search_state()
        self.addCleanup(recommendation_providers.clear_search_state)
        self.settings = {"enabled": True, "model_id": "nature-config", "seed_keywords": ["birds"],
                         "allow_unverified_links": False, "revision": 1}
        self.history = []
        self.local_videos = []
        self.catalog = []
        self.patches = [
            patch.object(config, "RECOMMENDATION_CONNECTOR_URL", "http://127.0.0.1:8301", create=True),
            patch.object(db, "get_recommendation_settings", side_effect=lambda: copy.deepcopy(self.settings), create=True),
            patch.object(db, "update_recommendation_settings", side_effect=self.update_settings, create=True),
            patch.object(db, "list_watch_history", side_effect=lambda limit=30: copy.deepcopy(self.history[:limit]), create=True),
            patch.object(db, "list_videos", side_effect=lambda status=None: copy.deepcopy(self.local_videos)),
            patch.object(db, "get_video", return_value=None),
            patch.object(db, "list_catalog_candidates", side_effect=lambda providers, limit=600: copy.deepcopy(self.catalog)),
            patch.object(db, "store_discoveries"),
            patch.object(db, "record_recommendations"),
            patch.object(db, "filter_presented_links", side_effect=lambda items, session_id: list(items)),
            patch.object(db, "record_presented_links", side_effect=lambda items, session_id: list(items)),
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
        items = items if items is not None else [video(), video("rumble", "v123abc")]
        playlist = playlist if playlist is not None else [{"id": item["id"], "reason": "Related topic"} for item in items]
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
                        {"enabled": "true"}, {"seed_keywords": None}, {"custom_prompt": None},
                        {"custom_prompt": "x" * 2001}, {"custom_prompt": "bad\x00prompt"},
                        {"connector_url": "https://evil.test"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.patch("/api/recommendations/settings", json=payload).status_code, 422)

    def test_ai_title_fallback_requires_and_validates_selected_model(self):
        self.settings["model_id"] = None
        self.assertEqual(self.client.patch("/api/recommendations/settings",
                         json={"allow_ai_title_lookup": True}).status_code, 400)
        self.settings["model_id"] = "nature-config"
        with patch.object(connector_client, "discover_models",
                          return_value={"models": [{"id": "nature-config"}]}):
            response = self.client.patch("/api/recommendations/settings",
                                         json={"allow_ai_title_lookup": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["allow_ai_title_lookup"])

    def test_seed_topics_generate_verified_playlist_and_ready_link(self):
        self.settings["custom_prompt"] = "Prefer concise field guides and new creators."
        self.local_videos = [{**video(), "id": "local-id", "status": "ready"}]
        completions, searches = self.complete_and_search()
        with completions as completion, searches:
            response = self.client.post("/api/recommendations", json={"context": "feed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["media_id"], "local-id")
        self.assertEqual(response.json()["keywords"], ["birds"])
        self.assertEqual(completion.call_args_list[0].args[0], "nature-config")
        self.assertIn('"seed_keywords": ["birds"]', completion.call_args_list[0].args[1][1]["content"])
        for call in completion.call_args_list:
            self.assertIn({
                "role": "user", "content": "Additional recommendation preferences supplied by the user:\n"
                "Prefer concise field guides and new creators."}, call.args[1])

    def test_history_source_limits_search_and_results_to_same_website(self):
        completions, searches = self.complete_and_search([video()], [{"id": video()["id"]}])
        with completions, searches as search:
            response = self.client.post("/api/recommendations", json={
                "context": "history", "source": "youtube"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["connector"] for item in response.json()["items"]], ["youtube"])
        self.assertEqual(search.call_args.args[3], "youtube")

    def test_opt_in_can_show_allowlisted_unverified_model_link(self):
        self.settings["allow_unverified_links"] = True
        replies = [assistant({"keywords": ["birds"]}), assistant({"playlist": [{
            "url": "https://youtu.be/BBBBBBBBBBB", "title": "A model suggestion",
            "reason": "Related wildlife"}]})]
        with patch.object(connector_client, "complete", side_effect=replies), \
                patch.object(tools, "search_by_key_words", return_value={"results": [], "warnings": []}):
            payload = self.client.post("/api/recommendations", json={}).json()
        self.assertEqual(payload["items"][0]["source_url"],
                         "https://www.youtube.com/watch?v=BBBBBBBBBBB")
        self.assertFalse(payload["items"][0]["verified"])
        self.assertEqual(payload["items"][0]["title"], "A model suggestion")

    def test_opt_in_accepts_model_url_fields_and_builtin_ids_without_search_candidates(self):
        self.settings["allow_unverified_links"] = True
        target = video(ident="BBBBBBBBBBB")
        selections = [target["source_url"], {"url": target["source_url"]},
                      {"source_url": "https://youtu.be/BBBBBBBBBBB?t=1"},
                      {"id": target["source_url"]}, {"id": target["id"]}, target["id"],
                      {"id": "missing", "url": "invalid", "source_url": target["source_url"]},
                      {"url": target["source_url"], "verified": True}]
        for selection in selections:
            with self.subTest(selection=selection):
                rec._CACHE.clear()
                completions, searches = self.complete_and_search([], [selection])
                with completions as completion, searches:
                    response = self.client.post("/api/recommendations", json={})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([item["id"] for item in response.json()["items"]], [target["id"]])
                self.assertFalse(response.json()["items"][0]["verified"])
                self.assertEqual(response.json()["items"][0]["verification"], "model")
                self.assertIn('"url":', completion.call_args.args[1][0]["content"])
                self.assertIn("If candidates are empty", completion.call_args.args[1][0]["content"])
        completions, searches = self.complete_and_search([], [{"id": "rumble:v123abc"}])
        with completions, searches:
            payload = self.client.post("/api/recommendations", json={"refresh": True}).json()
        self.assertEqual(payload["items"][0]["source_url"], "https://rumble.com/v123abc.html")
        self.assertFalse(payload["items"][0]["verified"])

    def test_candidate_url_aliases_remain_verified_for_both_toggle_values(self):
        for allow_unverified in (False, True):
            for field in ("url", "source_url", "id"):
                with self.subTest(allow_unverified=allow_unverified, field=field):
                    self.settings["allow_unverified_links"] = allow_unverified
                    rec._CACHE.clear()
                    selections = [{field: "https://youtu.be/abcdefghijk?t=4"},
                                  {"id": video()["id"]}]
                    completions, searches = self.complete_and_search([video()], selections)
                    with completions, searches:
                        payload = self.client.post("/api/recommendations", json={}).json()
                    self.assertEqual(len(payload["items"]), 1)
                    self.assertTrue(payload["items"][0]["verified"])
                    self.assertEqual(payload["items"][0]["title"], video()["title"])

    def test_unverified_model_references_are_removed_when_toggle_is_off(self):
        target = video(ident="BBBBBBBBBBB")
        selections = [{"url": target["source_url"]}, {"source_url": target["source_url"]},
                      {"id": target["source_url"]}, {"id": target["id"]}, target["source_url"],
                      {"id": video()["id"]}]
        completions, searches = self.complete_and_search([video()], selections)
        with completions, searches:
            payload = self.client.post("/api/recommendations", json={}).json()
        self.assertEqual([item["id"] for item in payload["items"]], [video()["id"]])
        self.assertTrue(payload["items"][0]["verified"])
        self.assertEqual(payload["warnings"], [])

    def test_unverified_discovery_flags_are_preserved_and_respect_toggle(self):
        for allow_unverified in (False, True):
            for marker in (False, None):
                with self.subTest(allow_unverified=allow_unverified, marker=marker):
                    self.settings["allow_unverified_links"] = allow_unverified
                    rec._CACHE.clear()
                    candidate = {**video(), "verified": marker, "verification": "web_search"}
                    if marker is None:
                        candidate.pop("verified")
                    completions, searches = self.complete_and_search([candidate])
                    with completions as completion, searches:
                        payload = self.client.post("/api/recommendations", json={}).json()
                    self.assertEqual(len(payload["items"]), int(allow_unverified))
                    if allow_unverified:
                        self.assertFalse(payload["items"][0]["verified"])
                        self.assertEqual(payload["items"][0]["verification"], "web_search")
                    else:
                        self.assertEqual(completion.call_count, 1)

    def test_unverified_tool_discovery_is_kept_only_after_opt_in(self):
        tool_message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-1", "type": "function", "function": {"name": "search_by_url",
            "arguments": json.dumps({"url": video()["source_url"]})}}]}
        candidate = {**video(), "verified": False, "verification": "custom_search"}
        for allow_unverified in (False, True):
            with self.subTest(allow_unverified=allow_unverified):
                rec._CACHE.clear()
                self.settings["allow_unverified_links"] = allow_unverified
                replies = [tool_message, assistant({"keywords": ["birds"]}),
                           assistant({"playlist": [{"id": candidate["id"]}]})]
                with patch.object(connector_client, "complete", side_effect=replies), \
                        patch.object(tools, "search_by_url", return_value=candidate), \
                        patch.object(tools, "search_by_key_words", return_value={"results": [], "warnings": []}):
                    payload = self.client.post("/api/recommendations", json={"source": "youtube"}).json()
                self.assertEqual(len(payload["items"]), int(allow_unverified))
                if allow_unverified:
                    self.assertFalse(payload["items"][0]["verified"])

    def test_opt_in_keeps_source_watch_history_current_and_exclusion_filters(self):
        self.settings["allow_unverified_links"] = True
        watched, excluded, current = [video(ident=letter * 11) for letter in "ABC"]
        self.history = [watched]
        selections = [{"url": item["source_url"]} for item in
                      [watched, excluded, current, video("rumble", "v123abc"), video()]]
        selections += [{"id": "youtube:abcdefghijk"}, {"url": "https://unknown.test/video/123"},
                       {"url": "https://www.youtube.com/playlist?list=123"}, {"id": {"invalid": "type"}}]
        completions, searches = self.complete_and_search([], selections)
        with completions, searches, patch.object(db, "get_video", return_value=current):
            payload = self.client.post("/api/recommendations", json={"source": "youtube", "video_id": "local",
                "exclude_urls": ["https://youtu.be/BBBBBBBBBBB"]}).json()
        self.assertEqual([item["id"] for item in payload["items"]], [video()["id"]])
        self.assertFalse(payload["items"][0]["verified"])

    def test_opt_in_can_rank_when_provider_search_fails(self):
        self.settings["allow_unverified_links"] = True
        completions, _ = self.complete_and_search([], [{"source_url": video()["source_url"]}])
        with completions, patch.object(tools, "search_by_key_words", side_effect=tools.ToolError("Search unavailable.")):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertFalse(response.json()["items"][0]["verified"])
        self.assertIn("Search unavailable.", response.json()["warnings"])

    def test_custom_provider_prompts_and_model_links_use_configured_source(self):
        self.settings.update(allow_unverified_links=True, providers=[
            {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True},
            {"id": "youtube", "name": "YouTube", "domain": "youtube.com", "enabled": False}])
        selections = [{"url": "https://vimeo.com/123456789", "title": "A Vimeo video"},
                      {"url": video()["source_url"]}, {"url": "https://vimeo.com.evil.test/123456789"}]
        completions, searches = self.complete_and_search([], selections)
        with completions as completion, searches as search:
            response = self.client.post("/api/recommendations", json={"source": "vimeo.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertEqual(response.json()["items"][0]["connector"], "vimeo.com")
        self.assertFalse(response.json()["items"][0]["verified"])
        self.assertEqual(search.call_args.args[3], "vimeo.com")
        self.assertEqual(search.call_args.kwargs["providers"], self.settings["providers"])
        for call in completion.call_args_list:
            prompt = json.loads(call.args[1][1]["content"])
            self.assertEqual(prompt["selected_source"], "vimeo.com")
            self.assertEqual([provider["id"] for provider in prompt["providers"]], ["vimeo.com"])

    def test_unconfigured_or_disabled_provider_stops_before_model_work(self):
        self.settings["providers"] = [
            {"id": "youtube", "name": "YouTube", "domain": "youtube.com", "enabled": False}]
        with patch.object(connector_client, "complete") as completion:
            for source in ("youtube", "vimeo.com"):
                with self.subTest(source=source):
                    response = self.client.post("/api/recommendations", json={"source": source})
                    self.assertEqual(response.status_code, 502)
                    self.assertIn("configured, enabled", response.json()["detail"])
            response = self.client.post("/api/recommendations", json={})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["items"], [])
        completion.assert_not_called()

    def test_disabling_unverified_links_invalidates_cached_results(self):
        self.settings["allow_unverified_links"] = True
        completions, searches = self.complete_and_search([], [{"url": video()["source_url"]}])
        with completions, searches:
            self.assertEqual(len(self.client.post("/api/recommendations", json={}).json()["items"]), 1)
        changed = self.client.patch("/api/recommendations/settings", json={"allow_unverified_links": False})
        self.assertEqual(changed.status_code, 200)
        completions, searches = self.complete_and_search([], [])
        with completions as completion, searches:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(completion.call_count, 1)

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
        self.assertEqual(payload["warnings"], [])

    def test_unusable_model_references_are_replaced_with_discovered_videos(self):
        first, second, third = video(), video("rumble", "v123abc"), video(ident="AAAAAAAAAAA")
        completions, searches = self.complete_and_search([first, second, third], [
            {"id": third["id"], "reason": "Model preference"}, {"id": "invented-id"},
            {"url": "https://unknown.test/video"}])
        with completions as completion, searches:
            payload = self.client.post("/api/recommendations", json={"limit": 2}).json()
        self.assertEqual([item["id"] for item in payload["items"]], [third["id"], first["id"]])
        self.assertEqual(payload["items"][0]["reason"], "Model preference")
        self.assertEqual(payload["items"][1]["reason"], "Found for your topics")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(completion.call_count, 2)

    def test_recovery_keeps_current_history_excluded_disabled_and_unverified_out(self):
        current, watched, excluded = [video(ident=letter * 11) for letter in "ABC"]
        hidden = {**video(ident="DDDDDDDDDDD"), "verified": False}
        self.history = [watched]
        self.settings["providers"] = [{**provider, "enabled": provider["id"] == "youtube"}
                                      for provider in default_providers()]
        candidates = [current, watched, excluded, hidden, video("rumble", "v123abc"), video()]
        completions, searches = self.complete_and_search(candidates, [{"id": "bad-reference"}] * 8)
        with completions, searches, patch.object(db, "get_video", return_value=current):
            payload = self.client.post("/api/recommendations", json={"video_id": "current",
                "exclude_urls": [excluded["source_url"]]}).json()
        self.assertEqual([item["id"] for item in payload["items"]], [video()["id"]])
        self.assertTrue(payload["items"][0]["verified"])
        self.assertFalse(any("removed" in warning for warning in payload["warnings"]))

    def test_discovered_unverified_recovery_requires_opt_in_and_keeps_provenance(self):
        candidate = {**video(), "verified": False, "verification": "custom_search"}
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                self.settings["allow_unverified_links"] = allowed
                rec._CACHE.clear()
                completions, searches = self.complete_and_search([candidate], [{"id": "bad-reference"}])
                with completions, searches:
                    payload = self.client.post("/api/recommendations", json={}).json()
                self.assertEqual(len(payload["items"]), int(allowed))
                if allowed:
                    self.assertFalse(payload["items"][0]["verified"])
                    self.assertEqual(payload["items"][0]["verification"], "custom_search")

    def test_discovered_custom_aliases_resolve_without_becoming_unverified(self):
        self.settings["providers"] = default_providers() + [
            {"id": "vimeo.com", "domain": "vimeo.com", "name": "Vimeo", "enabled": True}]
        candidate = {"id": "vimeo.com:123456789", "connector": "vimeo.com", "title": "Bird flight",
                     "source_url": "https://vimeo.com/123456789", "verified": True,
                     "verification": "provider_search"}
        for reference in ("vimeo:123456789", "123456789", 123456789, " vimeo.com:123456789 "):
            with self.subTest(reference=reference):
                rec._CACHE.clear()
                completions, searches = self.complete_and_search([candidate], [
                    {"id": reference, "reason": "Related flight"}])
                with completions, searches:
                    payload = self.client.post("/api/recommendations", json={}).json()
                self.assertEqual(payload["items"][0]["reason"], "Related flight")
                self.assertEqual(payload["items"][0]["id"], candidate["id"])
                self.assertTrue(payload["items"][0]["verified"])
                self.assertEqual(payload["warnings"], [])
        aliases = rec._candidate_aliases([candidate, {**candidate, "id": "other.com:123456789"}])
        self.assertIsNone(aliases["123456789"])
        self.assertEqual(aliases["vimeo:123456789"]["id"], candidate["id"])

    def test_youtube_current_video_can_recommend_other_enabled_websites(self):
        current = video(ident="CCCCCCCCCCC")
        target = video("rumble", "v123abc")
        completions, searches = self.complete_and_search([video(), target], [{"id": target["id"]}])
        with completions as completion, searches as lookup, patch.object(db, "get_video", return_value=current):
            payload = self.client.post("/api/recommendations", json={"context": "watch", "video_id": "current"}).json()
        self.assertEqual(payload["items"][0]["connector"], "rumble")
        self.assertEqual(lookup.call_args.args[3], "all")
        for call in completion.call_args_list:
            self.assertEqual(json.loads(call.args[1][1]["content"])["selected_source"], "all")
        self.assertIn("across enabled websites", completion.call_args.args[1][0]["content"])

    def test_empty_model_playlist_uses_weighted_random_fallback(self):
        completions, searches = self.complete_and_search([video()], [])
        with completions, searches:
            payload = self.client.post("/api/recommendations", json={}).json()
        self.assertEqual([item["id"] for item in payload["items"]], [video()["id"]])
        self.assertTrue(payload["fallback_used"])
        self.assertEqual(payload["items"][0]["fallback_source"], "custom_search")

    def test_two_sources_are_synthesized_and_fallback_uses_saved_percentages(self):
        custom = [{**video(ident=f"{number:011d}"), "origins": ["custom_search"]} for number in range(1, 9)]
        unverified_custom = [{**video(ident=f"{number:011d}"), "verified": False,
                              "verification": "custom_search", "origins": ["custom_search"]}
                             for number in range(10, 18)]
        self.catalog = [{**video(ident=f"{number:011d}"), "verified": False, "user_added": True,
                         "origins": ["watch_later"]} for number in range(20, 30)]
        self.settings["allow_unverified_links"] = True
        completions, searches = self.complete_and_search(custom + unverified_custom, [])
        with completions as completion, searches:
            response = self.client.post("/api/recommendations", json={"limit": 10})
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertTrue(response.json()["fallback_used"])
        self.assertEqual([sum(item["fallback_source"] == origin for item in items)
                          for origin in ("custom_search", "watch_later")], [5, 5])
        self.assertEqual(len({item["id"] for item in items}), 10)
        prompt = json.loads(completion.call_args.args[1][1]["content"])
        self.assertEqual({origin for item in prompt["candidates"] for origin in item["origins"]},
                         {"custom_search", "watch_later"})
        db.store_discoveries.assert_called_once()
        db.record_recommendations.assert_called_once()
        self.assertTrue(db.record_recommendations.call_args.kwargs["fallback"])

    def test_all_unusable_model_picks_use_fallback_percentages(self):
        saved = {**video(ident="AAAAAAAAAAA"), "verified": False, "user_added": True, "origins": ["watch_later"]}
        self.catalog = [saved]
        self.settings["fallback_weights"] = {"custom_search": 0, "public_search": 0, "watch_later": 100}
        completions, searches = self.complete_and_search([video()], [{"id": "invented-id"}])
        with completions, searches:
            payload = self.client.post("/api/recommendations", json={}).json()
        self.assertTrue(payload["fallback_used"])
        self.assertEqual([item["id"] for item in payload["items"]], [saved["id"]])
        self.assertEqual(payload["items"][0]["fallback_source"], "watch_later")

    def test_watch_later_can_start_without_history_topics_or_unverified_opt_in(self):
        self.settings["seed_keywords"] = []
        self.catalog = [{**video(), "verified": False, "user_added": True,
                         "origins": ["watch_later"], "description": "A" * 10000}]
        with patch.object(connector_client, "complete", return_value=assistant({"playlist": []})) as completion, \
                patch.object(tools, "search_by_key_words") as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["items"][0]["verified"])
        self.assertEqual(response.json()["items"][0]["fallback_source"], "watch_later")
        self.assertEqual(completion.call_count, 1)
        search.assert_not_called()
        prompt = json.loads(completion.call_args.args[1][1]["content"])
        self.assertEqual(len(prompt["candidates"][0]["description"]), 500)

    def test_empty_model_fallback_keeps_scope_exclusions_and_unverified_setting(self):
        current, watched, excluded, allowed = [video(ident=f"{number:011d}") for number in range(1, 5)]
        self.history = [watched]
        self.catalog = [{**current, "user_added": True}, {**watched, "user_added": True},
                        {**excluded, "user_added": True}, {**allowed, "user_added": True},
                        {**video("rumble", "v123abc"), "user_added": True}]
        unverified = {**video(ident="unverified1"), "verified": False, "verification": "web_search"}
        completions, searches = self.complete_and_search([unverified], [])
        with completions, searches, patch.object(db, "get_video", return_value=current):
            response = self.client.post("/api/recommendations", json={"source": "youtube", "video_id": "local",
                "exclude_urls": [excluded["source_url"]]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["items"]], [allowed["id"]])

    def test_duplicate_saved_and_discovered_video_keeps_all_origins(self):
        self.catalog = [{**video(), "title": "My chosen title", "user_added": True,
                         "verified": False, "origins": ["watch_later"]}]
        completions, searches = self.complete_and_search([{**video(), "origins": ["custom_search"]}], [])
        with completions, searches:
            payload = self.client.post("/api/recommendations", json={}).json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["title"], "My chosen title")
        self.assertTrue(payload["items"][0]["verified"])
        self.assertEqual(set(payload["items"][0]["origins"]), {"watch_later", "custom_search"})

    def test_model_topic_failure_searches_seed_interests_then_uses_fallback(self):
        with patch.object(connector_client, "complete", side_effect=connector_client.ConnectorError("private upstream error")) as completion, \
                patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}) as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["fallback_used"])
        self.assertEqual(search.call_args.args[0], ["birds"])
        self.assertEqual(completion.call_count, 1)

    def test_fetch_all_setting_is_applied_to_final_recommendation_discovery(self):
        self.settings["fetch_all_search_links"] = True
        completions, _ = self.complete_and_search()
        with completions, patch.object(tools, "search_by_key_words", return_value={
                "results": [video()], "warnings": []}) as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(search.call_args.kwargs["fetch_all"])
        self.assertNotIn("private upstream error", response.text)

    def test_saved_url_merges_new_unverified_discovery_before_visibility_filter(self):
        self.catalog = [{**video(), "title": video()["source_url"], "verified": False,
                         "user_added": True, "user_title": None, "user_description": None,
                         "origins": ["watch_later"]}]
        discovered = {**video(), "verified": False, "verification": "custom_search",
                      "origins": ["custom_search"]}
        completions, searches = self.complete_and_search([discovered], [])
        with completions, searches:
            payload = self.client.post("/api/recommendations", json={}).json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["title"], video()["title"])
        self.assertEqual(set(payload["items"][0]["origins"]), {"watch_later", "custom_search"})
        self.assertFalse(payload["items"][0]["verified"])

    def test_model_ranking_failure_uses_saved_candidates_without_repeating_model_work(self):
        self.catalog = [{**video(), "verified": False, "user_added": True, "origins": ["watch_later"]}]
        with patch.object(connector_client, "complete", side_effect=[assistant({"keywords": ["birds"]}),
                connector_client.ConnectorError("private upstream error")]) as completion, \
                patch.object(tools, "search_by_key_words", return_value={"results": [], "warnings": []}):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["fallback_used"])
        self.assertEqual(completion.call_count, 2)
        self.assertNotIn("private upstream error", response.text)

    def test_online_deadline_reserves_time_for_watch_later_fallback(self):
        self.catalog = [{**video(), "verified": False, "user_added": True, "origins": ["watch_later"]}]
        for stage in ("topics", "search", "ranking"):
            with self.subTest(stage=stage):
                rec._CACHE.clear()
                now, calls = [100.0], []
                def complete(*args, **kwargs):
                    calls.append(kwargs)
                    if stage == "topics" or (stage == "ranking" and len(calls) == 2):
                        now[0] = 100 + rec.REQUEST_DEADLINE - rec.FALLBACK_RESERVE + 1
                    return assistant({"keywords": ["birds"]} if len(calls) == 1 else {"playlist": []})
                def search(keywords, limit, guard, *args, **kwargs):
                    if stage == "search":
                        now[0] = 100 + rec.REQUEST_DEADLINE - rec.FALLBACK_RESERVE + 1
                    guard()
                    return {"results": [], "warnings": []}
                with patch.object(rec, "time", SimpleNamespace(monotonic=lambda: now[0])), \
                        patch.object(connector_client, "complete", side_effect=complete), \
                        patch.object(tools, "search_by_key_words", side_effect=search) as searches:
                    response = self.client.post("/api/recommendations", json={})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["fallback_used"])
                self.assertEqual(response.json()["items"][0]["fallback_source"], "watch_later")
                self.assertEqual(len(calls), 2 if stage == "ranking" else 1)
                if stage == "topics":
                    searches.assert_not_called()

    def test_total_deadline_still_stops_even_with_saved_candidates(self):
        self.catalog = [{**video(), "user_added": True, "origins": ["watch_later"]}]
        now = [100.0]
        def complete(*args, **kwargs):
            now[0] += rec.REQUEST_DEADLINE + 1
            return assistant({"keywords": ["birds"]})
        with patch.object(rec, "time", SimpleNamespace(monotonic=lambda: now[0])), \
                patch.object(connector_client, "complete", side_effect=complete), \
                patch.object(tools, "search_by_key_words") as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 502)
        search.assert_not_called()
        db.record_recommendations.assert_not_called()

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

    def test_recovered_final_search_does_not_repeat_preliminary_tool_failure(self):
        reports = [{"id": "youtube", "name": "YouTube", "status": "ok", "count": 1,
                    "discovery": "provider_search", "warnings": []}]
        replies = [{"role": "assistant", "content": None, "tool_calls": [{"id": "call-1", "type": "function",
                    "function": {"name": "search_by_key_words", "arguments": '{"keywords":["birds"]}'}}]},
                   assistant({"keywords": ["wildlife"]}), assistant({"playlist": [video()["id"]]})]
        with patch.object(connector_client, "complete", side_effect=replies), \
                patch.object(tools, "search_by_key_words", side_effect=[
                    {"results": [], "warnings": ["Search was temporarily unavailable."]},
                    {"results": [video()], "warnings": [], "sources": reports}]):
            payload = self.client.post("/api/recommendations", json={}).json()
        self.assertEqual(payload["items"][0]["id"], video()["id"])
        self.assertEqual(payload["sources"], reports)
        self.assertEqual(payload["warnings"], [])

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
             patch.object(tools, "search_by_key_words", return_value={"results": [], "warnings": []}) as search:
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(completion.call_count, 4)
        search.assert_called_once()

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

    def test_repeated_bad_playlist_uses_candidates_without_more_model_retries(self):
        replies = [assistant({"keywords": ["birds"]}), assistant({"youtube": [video()["source_url"]]}),
                   {"role": "assistant", "content": "private model text"}]
        with patch.object(connector_client, "complete", side_effect=replies) as completion, \
                patch.object(tools, "search_by_key_words", return_value={"results": [video()], "warnings": []}):
            response = self.client.post("/api/recommendations", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], video()["id"])
        self.assertTrue(response.json()["fallback_used"])
        self.assertNotIn("private model text", response.text)
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
    def setUp(self):
        recommendation_providers.clear_search_state()
        self.addCleanup(recommendation_providers.clear_search_state)
        patcher = patch.object(db, "get_recommendation_settings", return_value={"providers": default_providers()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_custom_search_uses_plain_topics_and_recovers_with_second_topic(self):
        provider = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
        candidate = {"id": "vimeo.com:123", "connector": "vimeo.com", "title": "Birds",
                     "source_url": "https://vimeo.com/123", "verified": True}
        responses = [{"results": [], "warnings": [], "status": "empty"},
                     {"results": [candidate], "warnings": []}]
        with patch.object(recommendation_providers, "search_website", side_effect=responses) as lookup, \
                patch.object(tools.search, "search_videos") as builtin:
            result = tools.search_by_key_words(["very specific topic", "birds", "unused topic"], providers=[provider])
        self.assertEqual([call.args[1] for call in lookup.call_args_list], ["very specific topic", "birds"])
        self.assertEqual(result["results"], [candidate])
        self.assertEqual(result["warnings"], [])
        builtin.assert_not_called()

    def test_custom_search_failure_does_not_hide_another_source_or_retry_forever(self):
        provider = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
        found = SearchResult(**{key: value for key, value in video().items() if key != "verified"})
        with patch.object(recommendation_providers, "search_website", side_effect=tools.search.SearchError("Vimeo is temporarily unavailable.")) as lookup, \
                patch.object(tools.search, "search_videos", return_value=SearchResponse(
                    query="birds", source="youtube", results=[found], warnings=[])):
            result = tools.search_by_key_words(["birds", "nature", "wildlife"],
                                              providers=[default_providers()[0], provider])
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual([item["id"] for item in result["results"]], [video()["id"]])
        self.assertEqual(result["warnings"], ["Vimeo is temporarily unavailable."])

    def test_returned_search_failure_survives_later_empty_topic(self):
        provider = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
        responses = [{"results": [], "warnings": ["Vimeo is temporarily unavailable."], "status": "unavailable"},
                     {"results": [], "warnings": [], "status": "empty"}]
        with patch.object(recommendation_providers, "search_website", side_effect=responses) as lookup:
            result = tools.search_by_key_words(["birds", "nature", "unused"], providers=[provider])
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["warnings"], ["Vimeo is temporarily unavailable."])
        self.assertEqual(result["sources"][0]["status"], "unavailable")

    def test_source_reports_preserve_success_alongside_unavailable_website(self):
        provider = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
        found = SearchResult(**{key: value for key, value in video().items() if key != "verified"})
        with patch.object(recommendation_providers, "search_website", return_value={
                "results": [], "warnings": ["Vimeo search is unavailable."], "status": "unavailable", "discovery": "custom_search"}), \
                patch.object(tools.search, "search_videos", return_value=SearchResponse(
                    query="birds", source="youtube", results=[found], warnings=[])):
            result = tools.search_by_key_words(["birds"], providers=[default_providers()[0], provider])
        self.assertEqual([item["id"] for item in result["results"]], [video()["id"]])
        self.assertEqual(result["sources"], [
            {"id": "youtube", "name": "YouTube", "status": "ok", "count": 1,
             "discovery": "provider_search", "warnings": []},
            {"id": "vimeo.com", "name": "Vimeo", "status": "unavailable", "count": 0,
             "discovery": "custom_search", "warnings": ["Vimeo search is unavailable."]}])

    def test_cached_source_report_retains_verification_and_age(self):
        provider = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
        candidate = {"id": "vimeo.com:123", "connector": "vimeo.com", "source_url": "https://vimeo.com/123",
                     "title": "Birds", "verified": True, "verification": "provider_search"}
        with patch.object(recommendation_providers, "search_website", return_value={
                "results": [candidate], "warnings": ["Using recently found videos."], "status": "ok",
                "discovery": "provider_search", "cache_status": "stale", "cache_age_seconds": 150}):
            result = tools.search_by_key_words(["birds"], providers=[provider])
        self.assertTrue(result["results"][0]["verified"])
        self.assertEqual(result["sources"][0]["status"], "cached")
        self.assertEqual(result["sources"][0]["cache_age_seconds"], 150)
        self.assertEqual(result["sources"][0]["count"], 1)

    def test_no_matching_links_report_is_empty_not_provider_failure(self):
        provider = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
        with patch.object(recommendation_providers, "search_website", return_value={
                "results": [], "warnings": ["Vimeo search returned no matching video links."],
                "status": "empty", "discovery": "custom_search"}):
            result = tools.search_by_key_words(["birds"], providers=[provider])
        self.assertEqual(result["sources"][0]["status"], "empty")

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

    def test_custom_url_uses_provider_metadata_extractor(self):
        provider = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
        data = {"id": "123", "title": "Provider title", "duration": 30, "uploader": "Wildlife"}
        with patch.object(tools.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")) as run:
            result = tools.search_by_url("https://vimeo.com/123", providers=[provider])
        self.assertEqual(result["title"], "Provider title")
        self.assertEqual(result["connector"], "vimeo.com")
        self.assertTrue(result["verified"])
        self.assertEqual(run.call_args.args[0][-1], "https://vimeo.com/123")

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
