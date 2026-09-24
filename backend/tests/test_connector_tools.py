"""Connector agent contracts using isolated SQLite and no network providers."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from app import config
from app.schemas.recommendation_providers import default_providers
from app.schemas.search import SearchResponse, SearchResult
from app.services import connector_activity, connector_tools as tools, db, recommendations

BASE = "http://127.0.0.1:8000"
URL = "https://www.youtube.com/watch?v=abcdefghijk"


class ConnectorToolsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        self.enterContext(patch.object(tools, "_TASKS", {}))
        db.init_db()
        connector_activity.init_db()

    def call(self, name, **arguments):
        return tools.dispatch("clipfeed_" + name, arguments, BASE)

    def video(self, video_id="one", source_url=URL, **changes):
        path = self.folder / f"{video_id}.mp4"
        path.write_bytes(b"test media")
        db.create_video(video_id, source_url, "youtube", "best", self.folder)
        db.update_video(video_id, status="ready", title="River birds", uploader="Nature channel",
                        description="Field guide", completed_at="2026-01-02T12:00:00+00:00",
                        file_path=str(path), thumbnail_path=str(self.folder / "private.jpg"), **changes)
        return db.get_video(video_id)

    def wait_task(self, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            task = self.call("get_task", task_id=task_id)
            if task["status"] != "pending":
                return task
            threading.Event().wait(0.005)
        self.fail("Background task did not complete")

    def test_all_histories_are_pageable_beyond_first_hundred_with_stable_oldest_order(self):
        with db._LOCK, db._connect() as conn:
            for index in range(125):
                ident = f"video-{index:03}"
                url = f"https://www.youtube.com/watch?v={index:011}"
                conn.execute("INSERT INTO videos (id,source_url,connector,status,title,created_at,completed_at)"
                             " VALUES (?,?,?,'ready',?,?,?)",
                             (ident, url, "youtube", ident, "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"))
                conn.execute("INSERT INTO watch_history (video_id,source_url,connector,title,watched_seconds,last_watched_at)"
                             " VALUES (?,?,?,?,10,?)", (ident, url, "youtube", ident, "2026-01-03T00:00:00+00:00"))
        catalog = [{"source_url": f"https://www.youtube.com/watch?v={index:011}", "title": f"video-{index:03}"}
                   for index in range(125)]
        db.add_watch_later(catalog)
        db.record_recommendations(catalog, default_providers())
        for tool in ("list_downloaded_videos", "list_watch_history", "list_watch_later", "list_recommendation_history"):
            with self.subTest(tool=tool):
                first = self.call(tool, limit=100)
                last = self.call(tool, limit=100, offset=first["next_offset"])
                self.assertEqual(first["total"], 125)
                self.assertEqual(len(first["items"]), 100)
                self.assertEqual(len(last["items"]), 25)
                self.assertIsNone(last["next_offset"])
                all_urls = [item["source_url"] for item in first["items"] + last["items"]]
                self.assertEqual(len(set(all_urls)), 125)
                self.assertTrue(all_urls[0].endswith("00000000000"))
                self.assertTrue(all_urls[-1].endswith("00000000124"))

    def test_filtering_metadata_dates_keywords_and_completion_is_literal(self):
        self.video()
        db.begin_video_keyword_generation("one", "test-model")
        db.finish_video_keyword_generation("one", ["birds", "rivers"])
        db.record_watch("one", 30, 30, completed=True)
        result = self.call("list_downloaded_videos", source="youtube", query="field", uploader="nature",
                           keyword="BIRDS", date_from="2026-01-02", date_to="2026-01-02")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["description"], "Field guide")
        self.assertEqual(result["items"][0]["downloaded_date"], "2026-01-02T12:00:00+00:00")
        for kwargs in ({"source": "rumble"}, {"query": "%' OR 1=1--"}, {"keyword": "ocean"},
                       {"date_to": "2026-01-01"}, {"date_from": "2026-01-03"}):
            self.assertEqual(self.call("list_downloaded_videos", **kwargs)["total"], 0)
        self.assertEqual(self.call("list_watch_history", completed=True)["total"], 1)
        self.assertEqual(self.call("list_watch_history", completed=False)["total"], 0)
        db.delete_video("one")
        watched = self.call("list_watch_history")["items"][0]
        self.assertTrue(watched["completed"])
        self.assertEqual(watched["watched_seconds"], 30)
        self.assertFalse(watched["playable"])

    def test_ready_playback_is_absolute_and_metadata_cannot_break_video_fence(self):
        self.video()
        db.update_video("one", title="Title\n```\n[link](javascript:alert(1))", error_message=str(self.folder))
        result = self.call("list_downloaded_videos")
        item = result["items"][0]
        self.assertEqual(item["stream_url"], BASE + "/api/media/one/stream")
        self.assertEqual(result["video_markdown"].count("```"), 2)
        decoded = json.loads(result["video_markdown"].split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(decoded[0]["title"], item["title"])
        self.assertNotIn(str(self.folder), json.dumps(result))
        db.update_video("one", file_path=str(self.folder / "missing.mp4"))
        missing = self.call("get_download_status", video_ids=["one", "absent"])
        self.assertFalse(missing["items"][0]["playable"])
        self.assertNotIn("video_markdown", missing)
        self.assertEqual(missing["missing_video_ids"], ["absent"])

    def test_batch_download_validates_every_url_before_mutation_and_reuses_jobs(self):
        self.video()
        with patch.object(tools.library, "start_download") as start:
            for bad in ("file:///etc/passwd", "http://localhost/video", "http://127.0.0.1/video",
                        "http://192.168.1.1/video", "http://[::1]/video", "https://user:secret@youtube.com/video",
                        "http://2130706433/video", "http://0300.0250.0.1/video"):
                with self.subTest(bad=bad), self.assertRaises(ValidationError):
                    self.call("download_videos", urls=[URL, bad])
            with self.assertRaises(ValidationError):
                self.call("download_videos", urls=[URL], execute_shell="bad")
            start.assert_not_called()
            result = self.call("download_videos", urls=[URL, "https://youtu.be/abcdefghijk?t=30"])
            start.assert_not_called()
            self.assertEqual(len(result["items"]), 1)
            self.assertTrue(result["items"][0]["reused"])
            db.update_video("one", status="queued", file_path=None)
            pending = self.call("download_videos", urls=[URL])
            self.assertTrue(pending["items"][0]["reused"])
            self.assertFalse(pending["items"][0]["playable"])

    def test_download_submission_starts_work_without_waiting_for_completion(self):
        row = {"id": "job", "source_url": URL, "status": "queued", "connector": "youtube"}
        with patch.object(tools.library, "start_download", return_value=row) as start:
            result = self.call("download_videos", urls=[URL], quality="720p")
        start.assert_called_once_with(URL, "720p")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["items"][0]["id"], "job")
        self.assertFalse(result["items"][0]["reused"])

    def test_search_returns_poll_handle_and_preserves_thumbnails_and_playback(self):
        self.video()
        release = threading.Event()
        started = threading.Event()

        def search(*arguments):
            started.set()
            release.wait(timeout=3)
            return SearchResponse(query="birds", source="all", warnings=[], results=[
                SearchResult(id="youtube:abcdefghijk", title="Birds", source_url=URL, connector="youtube"),
                SearchResult(id="youtube:otherabcdef", title="Remote", source_url="https://www.youtube.com/watch?v=otherabcdef",
                             connector="youtube", thumbnail_url="https://i.ytimg.com/vi/otherabcdef/default.jpg")])

        with patch.object(tools.manual_video_search, "search_videos", side_effect=search):
            task = self.call("search_videos", query="birds")
            self.assertTrue(started.wait(timeout=1))
            try:
                self.assertEqual(self.call("get_task", task_id=task["task_id"])["status"], "pending")
            finally:
                release.set()
            done = self.wait_task(task["task_id"])
        self.assertEqual(done["status"], "completed")
        results = done["result"]["results"]
        self.assertTrue(results[0]["playable"])
        self.assertFalse(results[1]["playable"])
        self.assertIn("![Video thumbnail]", results[1]["thumbnail_markdown"])
        self.assertNotIn(results[1]["source_url"], done["result"]["video_markdown"])

    def test_generation_filters_do_not_mutate_saved_preferences(self):
        before = db.get_recommendation_settings()
        with patch.object(recommendations, "recommend", return_value={"items": [], "status": "ready"}) as recommend:
            task = self.call("generate_recommendations", source="youtube", context="history", limit=3,
                             refresh=True, exclude_urls=[URL])
            done = self.wait_task(task["task_id"])
        self.assertEqual(done["status"], "completed")
        request = recommend.call_args.args[0]
        self.assertEqual(request.source, "youtube")
        self.assertEqual(request.exclude_urls, [URL])
        self.assertEqual(db.get_recommendation_settings(), before)

    def test_hint_updates_and_clears_saved_setting_without_changing_enablement(self):
        updated = self.call("set_recommendation_hint", hint="  Short nature videos\nNew creators  ")
        self.assertEqual(updated["custom_prompt"], "Short nature videos\nNew creators")
        self.assertFalse(updated["enabled"])
        self.call("set_recommendation_hint", hint="")
        self.assertEqual(db.get_recommendation_settings()["custom_prompt"], "")
        with self.assertRaises(ValidationError):
            self.call("set_recommendation_hint", hint="hello\x00")

    def test_add_watch_later_is_atomic_deduplicated_and_can_schedule_metadata(self):
        second = "https://www.youtube.com/watch?v=zyxwvutsrqp"
        with patch.object(tools.watch_later_titles, "schedule_items", side_effect=lambda items: items) as titles:
            result = self.call("add_watch_later", videos=[
                {"source_url": URL, "title": "Birds"},
                {"source_url": "https://youtu.be/abcdefghijk", "description": "Updated note"},
                {"source_url": second}], fetch_titles=True)
        self.assertEqual(result["added"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["title"], "Birds")
        self.assertEqual(result["items"][0]["description"], "Updated note")
        titles.assert_called_once()
        with patch.object(tools.watch_later_thumbnails, "schedule_items", side_effect=lambda items: items) as thumbnails:
            repeated = self.call("add_watch_later", videos=[{"source_url": URL}], fetch_titles=False)
        self.assertEqual(repeated["added"], 0)
        self.assertEqual(repeated["updated"], 1)
        thumbnails.assert_called_once()
        before = self.call("list_watch_later")["total"]
        with self.assertRaises(ValueError):
            self.call("add_watch_later", videos=[{"source_url": second},
                                                  {"source_url": "https://example.invalid/not-video"}])
        self.assertEqual(self.call("list_watch_later")["total"], before)

    def test_add_watch_later_accepts_agent_bracket_and_short_rumble_batch(self):
        urls = [
            "[https://www.youtube.com/watch?v=fR6Qd0B0ZUU",
            "https://rumble.com/v7cms2y",
            "https://www.youtube.com/watch?v=4Y40VcYk0b0",
            "https://rumble.com/v7cv6s4",
            "https://www.youtube.com/watch?v=Althvag63pw",
            "https://www.youtube.com/watch?v=yBLNtVtajPw",
            "https://www.youtube.com/watch?v=5vEVJ4dWpqY",
            "https://www.youtube.com/watch?v=VtRIRJ0tBRc",
            "https://www.youtube.com/watch?v=b5wQJ1mwdTs",
        ]
        with patch.object(tools.watch_later_titles, "schedule_items", side_effect=lambda items: items) as titles:
            result = self.call("add_watch_later", videos=[
                {"source_url": url, "title": None, "description": None} for url in urls
            ], fetch_titles=True)
        self.assertEqual((result["added"], result["updated"]), (9, 0))
        self.assertEqual(len(result["items"]), 9)
        self.assertEqual(result["items"][0]["source_url"], urls[0][1:])
        self.assertEqual(result["items"][1]["source_url"], "https://rumble.com/v7cms2y.html")
        self.assertEqual(result["items"][3]["source_url"], "https://rumble.com/v7cv6s4.html")
        self.assertEqual(self.call("list_watch_later")["total"], 9)
        titles.assert_called_once()

    def test_invalid_watch_later_entry_identifies_its_position_without_partial_save(self):
        with self.assertRaisesRegex(ValueError, "Video 2 has an invalid URL"):
            self.call("add_watch_later", videos=[{"source_url": URL},
                                                  {"source_url": "https://rumble.com/c/channel"}],
                      fetch_titles=False)
        self.assertEqual(self.call("list_watch_later")["total"], 0)

    def test_invalid_filters_and_unknown_tools_fail_before_submission(self):
        for tool, arguments in (("list_downloaded_videos", {"limit": 101}),
                                ("list_watch_history", {"offset": -1}),
                                ("list_watch_later", {"date_from": "yesterday"}),
                                ("list_recommendation_history", {"date_from": "2026-02-01", "date_to": "2026-01-01"}),
                                ("search_videos", {"query": " "}),
                                ("generate_recommendations", {"limit": "3"})):
            with self.subTest(tool=tool), self.assertRaises(ValidationError):
                self.call(tool, **arguments)
        self.assertEqual(tools._TASKS, {})
        with self.assertRaises(LookupError):
            self.call("delete_everything")
        with self.assertRaises(LookupError):
            self.call("get_task", task_id="from-old-process")

    def test_advertised_tools_have_strict_schemas_and_recommendation_history_is_labeled(self):
        definitions = tools.tool_definitions()
        self.assertEqual(len({item["name"] for item in definitions}), len(definitions))
        for item in definitions:
            self.assertFalse(item["parameters"]["additionalProperties"])
        db.record_recommendations([{"source_url": URL, "title": "Birds"}], default_providers(), fallback=True)
        history = self.call("list_recommendation_history", fallback=True)
        self.assertEqual(history["history_kind"], "generated_recommendations")
        self.assertEqual(history["total"], 1)
        self.assertEqual(self.call("list_recommendation_history", fallback=False)["total"], 0)

    def test_activity_tools_return_public_events_and_tag_totals(self):
        with patch.object(connector_activity, "list_events", return_value={"items": [
            {"id": "event", "type": "search", "payload": {"query": "birds"}, "created_at": "2026-01-01",
             "last_error": "private diagnostics", "attempts": 1, "delivered_at": None}],
            "total": 1, "next_offset": None}) as events:
            result = self.call("list_activity", event_type="search", date_from="2026-01-01")
        self.assertNotIn("last_error", result["items"][0])
        self.assertEqual(events.call_args.kwargs["since"], "2026-01-01T00:00:00+00:00")
        with patch.object(connector_activity, "recent_tags", return_value=[{"keyword": "birds", "watched_seconds": 120}]):
            tags = self.call("recent_tags")
        self.assertEqual(tags["items"][0]["keyword"], "birds")
        self.assertEqual(tags["recent_days"], 30)


if __name__ == "__main__":
    unittest.main()
