"""Connector HTTP boundaries and real search/playback observation wiring."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import config
from app.main import app
from app.routers import connector
from app.schemas.search import SearchResponse, SearchResult
from app.services import connector_activity as activity, connector_bridge, connector_tools, db, search


class ConnectorApiTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        db.init_db()
        # No lifespan: these tests exercise HTTP handlers without starting workers.
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.video = {"id": "youtube:abcdefghijk", "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                      "title": "River birds", "connector": "youtube"}

    def enable(self):
        response = self.client.patch("/api/connector/settings", json={"enabled": True})
        self.assertEqual(response.status_code, 200)
        return activity.get_state()["token"]

    def hook(self, token, name="clipfeed_list_downloaded_videos", **kwargs):
        return self.client.post(f"/api/connector/hooks/{token}/{name}", **kwargs)

    def test_opt_in_settings_are_strict_and_never_return_callback_secret(self):
        status = self.client.get("/api/connector/status").json()
        self.assertFalse(status["enabled"])
        self.assertNotIn("token", status)
        self.assertEqual(self.client.post("/api/connector/connect").status_code, 409)
        for body in ({"enabled": "true"}, {"enabled": None}, {"token": "override"},
                     {"base_url": None}, {"base_url": "file:///tmp/media"},
                     {"base_url": "http://0.0.0.0:8000"}, {"base_url": "https://user:secret@example.com"},
                     {"base_url": "http://localhost:8000/path"}, {"base_url": "http://localhost:0"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch("/api/connector/settings", json=body).status_code, 422)
        saved = self.client.patch("/api/connector/settings", json={"base_url": "http://localhost:9000/"}).json()
        self.assertEqual(saved["base_url"], "http://localhost:9000")
        db.init_db()
        self.assertEqual(activity.get_state()["base_url"], saved["base_url"])

    def test_callbacks_require_enabled_current_secret_including_unicode_tokens(self):
        token = activity.get_state()["token"]
        self.assertEqual(self.hook(token, json={}).status_code, 403)
        self.enable()
        for invalid in ("wrong-token", "\u2603"):
            self.assertEqual(self.hook(invalid, json={}).status_code, 403)
        self.assertEqual(self.hook(token, json={}).status_code, 200)
        self.client.patch("/api/connector/settings", json={"enabled": False})
        self.assertNotEqual(activity.get_state()["token"], token)
        self.enable()
        self.assertEqual(self.hook(token, json={}).status_code, 403)
        self.assertEqual(self.hook(activity.get_state()["token"], json={}).status_code, 200)

    def test_callbacks_take_raw_arguments_validate_and_enforce_size_limit(self):
        token = self.enable()
        self.assertEqual(self.hook(token, content="not json").status_code, 400)
        self.assertEqual(self.hook(token, json=[]).status_code, 400)
        self.assertEqual(self.hook(token, json={"arguments": {}}).status_code, 422)
        self.assertEqual(self.hook(token, json={"limit": 101}).status_code, 422)
        self.assertEqual(self.hook(token, name="no-such-tool", json={}).status_code, 400)
        self.assertEqual(self.hook(token, content=b" " * (64 * 1024 + 1)).status_code, 413)
        definitions = self.client.get("/api/connector/tools").json()["tools"]
        self.assertEqual(len(definitions), 13)
        self.assertNotIn(token, json.dumps(definitions))
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertFalse(any("hooks/" in path for path in paths))

    def test_opt_out_during_body_upload_prevents_dispatch(self):
        token = self.enable()

        async def receive():
            connector_bridge.configure({"enabled": False})
            return {"type": "http.request", "body": b"{}", "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
        with patch.object(connector_tools, "dispatch") as dispatch:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(connector.invoke(token, "clipfeed_list_downloaded_videos", request))
        self.assertEqual(raised.exception.status_code, 403)
        dispatch.assert_not_called()

    def test_hint_callback_persists_without_enabling_ai(self):
        token = self.enable()
        result = self.hook(token, name="clipfeed_set_recommendation_hint", json={"hint": "More nature documentaries"})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["enabled"])
        self.assertEqual(db.get_recommendation_settings()["custom_prompt"], "More nature documentaries")
        self.assertEqual(self.hook(token, name="clipfeed_set_recommendation_hint", json={"hint": ""}).status_code, 200)
        self.assertEqual(db.get_recommendation_settings()["custom_prompt"], "")
        with patch.object(connector_tools, "dispatch", side_effect=db.SettingsConflictError("changed")):
            self.assertEqual(self.hook(token, name="clipfeed_set_recommendation_hint", json={"hint": "new"}).status_code, 409)

    def test_watch_later_bad_url_response_identifies_entry(self):
        token = self.enable()
        response = self.hook(token, name="clipfeed_add_watch_later", json={"videos": [
            {"source_url": self.video["source_url"]},
            {"source_url": "https://rumble.com/c/channel"},
        ]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Video 2", response.json()["detail"])
        self.assertEqual(db.list_watch_later()["total"], 0)

    def test_impressions_are_opt_in_and_deduplicated(self):
        body = {"event_id": "visible-cards", "context": "feed", "items": [self.video]}
        self.assertFalse(self.client.post("/api/connector/impressions", json=body).json()["recorded"])
        self.enable()
        for _ in range(2):
            self.assertTrue(self.client.post("/api/connector/impressions", json=body).json()["recorded"])
        events = activity.list_events(event_type="recommendation_impressions")["items"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["items"][0]["title"], "River birds")
        for changes in ({"context": "invented"}, {"items": []}, {"event_id": "bad id"},
                        {"items": [{**self.video, "source_url": "javascript:alert(1)"}]}):
            self.assertEqual(self.client.post("/api/connector/impressions", json={**body, **changes}).status_code, 422)

    def test_oversized_impressions_fail_cleanly_without_partial_event(self):
        self.enable()
        body = {"event_id": "oversized", "context": "watch",
                "items": [{**self.video, "description": "\u9ce5" * 10000}] * 20}
        self.assertEqual(self.client.post("/api/connector/impressions", json=body).status_code, 422)
        self.assertEqual(activity.status()["total"], 0)

    def test_actual_watch_route_batches_elapsed_time_and_reports_finished(self):
        self.enable()
        db.create_video("one", self.video["source_url"], "youtube", "best", self.folder)
        db.update_video("one", status="ready", title="River birds", duration=120)
        db.begin_video_keyword_generation("one", "test-model")
        db.finish_video_keyword_generation("one", ["birds"])
        body = {"video_id": "one", "watched_seconds": 30, "position_seconds": 999}
        self.assertEqual(self.client.post("/api/watch-history", json=body).status_code, 200)
        self.assertEqual(activity.status()["total"], 0)
        self.assertEqual(self.client.post("/api/watch-history", json=body).status_code, 200)
        event = activity.pending_events()[0]
        self.assertEqual(event["payload"]["watched_seconds"], 60)
        self.assertEqual(event["payload"]["position_seconds"], 120)
        self.assertEqual(event["payload"]["keywords"], ["birds"])
        self.assertEqual(self.client.post("/api/watch-history", json={**body, "watched_seconds": 0, "completed": True}).status_code, 200)
        self.assertEqual(activity.pending_events()[-1]["payload"]["status"], "finished")
        with patch.object(activity, "record_watch", side_effect=RuntimeError("unavailable")):
            self.assertEqual(self.client.post("/api/watch-history", json=body).status_code, 200)

    def test_actual_search_route_records_intent_not_load_more_duplicates(self):
        self.enable()
        result = SearchResponse(query="birds", source="youtube", warnings=[], results=[SearchResult(**self.video)])
        with patch.object(search, "search_videos", return_value=result):
            for limit in (12, 24):
                response = self.client.get("/api/search", params={"q": "birds", "source": "youtube",
                                                                 "limit": limit, "session": "one-intent"})
                self.assertEqual(response.status_code, 200)
        events = activity.list_events(event_type="search")["items"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["query"], "birds")
        self.assertEqual(events[0]["payload"]["result_count"], 1)

    def test_keyword_search_polls_are_not_duplicate_searches(self):
        self.enable()
        for _ in range(2):
            response = self.client.get("/api/video-keywords", params={"q": "birds", "session": "topic-search"})
            self.assertEqual(response.status_code, 200)
        self.client.get("/api/video-keywords", params={"q": "", "session": "topic-search"})
        events = activity.list_events(event_type="search")["items"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["source"], "library_keywords")


if __name__ == "__main__":
    unittest.main()
