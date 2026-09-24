"""Durability, elapsed-watch aggregation, and retry behavior of Connector logs."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.services import connector_activity as activity, db


class ConnectorActivityTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(config, "JOBS_DB_PATH", Path(folder) / "jobs.db"))
        db.init_db()
        activity.init_db()
        self.video = {"id": "video-one", "source_url": "https://video.example/watch/one",
                      "title": "Bird photography", "uploader": "Example creator",
                      "connector": "generic", "duration": 180,
                      "keywords": ["photography", "birds"]}

    def enable(self):
        return activity.update_state(enabled=True)

    def test_opt_in_state_and_outbox_survive_reinitialization(self):
        state = activity.get_state()
        self.assertFalse(state["enabled"])
        self.assertGreaterEqual(len(state["token"]), 32)
        activity.update_state(enabled=True, session_id="persisted-session",
                              registered_tools=["search", "download"])
        activity.record_search("birds", "library", 4, event_id="persistent-event")
        activity.init_db()
        after = activity.get_state()
        self.assertEqual(after["token"], state["token"])
        self.assertEqual(after["session_id"], "persisted-session")
        self.assertEqual(after["registered_tools"], ["search", "download"])
        self.assertEqual(activity.pending_events()[0]["id"], "persistent-event")
        with self.assertRaises(ValueError):
            activity.update_state(invalid_field=True)

    def test_disabled_activity_is_not_recorded(self):
        self.assertIsNone(activity.record_search("birds", "library", 3))
        self.assertIsNone(activity.record_watch(self.video, 65, 65, False))
        self.assertIsNone(activity.record_impressions([self.video], "recommendations"))
        self.assertEqual(activity.status(), {"total": 0, "pending": 0, "delivered": 0})
        self.assertEqual(activity.recent_tags(), [])

    def test_search_pagination_is_one_intent_but_new_queries_and_sessions_are_logged(self):
        self.enable()
        first = activity.record_search("Bird photography", "all", 12, session_id="first")
        activity.mark_delivered(first)
        activity.init_db()
        repeated = activity.record_search("  bird   photography ", "all", 24,
                                          session_id="first")
        self.assertEqual(repeated, first)
        self.assertEqual(activity.status()["total"], 1)
        self.assertEqual(activity.pending_events(), [])
        activity.record_search("Bird photography", "all", 12, session_id="second")
        activity.record_search("Bird photography", "library", 12, session_id="first")
        activity.record_search("Landscapes", "all", 12, session_id="first")
        self.assertEqual(activity.status()["total"], 4)
        with self.assertRaises(ValueError):
            activity.record_search("birds", "all", -1)
        with self.assertRaises(ValueError):
            activity.record_search("birds", "all", 1_000_001)

    def test_elapsed_time_aggregates_and_seek_position_does_not_count(self):
        self.enable()
        for index in range(3):
            self.assertIsNone(activity.record_watch(self.video, 15, 170, False,
                                                     event_id=f"report-{index}"))
        event_id = activity.record_watch(self.video, 15, 175, False, event_id="report-3")
        event = activity.pending_events()[0]
        self.assertEqual(event["id"], event_id)
        self.assertEqual(event["type"], "watch")
        self.assertEqual(event["payload"]["watched_seconds"], 60)
        self.assertEqual(event["payload"]["position_seconds"], 175)
        self.assertEqual(event["payload"]["keywords"], ["photography", "birds"])
        self.assertFalse(event["payload"]["finished"])
        self.assertEqual(activity.recent_tags()[0]["watched_seconds"], 60)

    def test_report_retry_and_delivery_retry_never_recount_watch_seconds(self):
        self.enable()
        event_id = activity.record_watch(self.video, 60, 70, False, event_id="same-report")
        activity.mark_failed(event_id, "temporarily unavailable")
        activity.init_db()
        self.assertIsNone(activity.record_watch(self.video, 60, 70, False,
                                                 event_id="same-report"))
        event = activity.pending_events()[0]
        self.assertEqual(event["id"], event_id)
        self.assertEqual(event["attempts"], 1)
        self.assertEqual(event["payload"]["watched_seconds"], 60)
        self.assertEqual(activity.recent_tags()[0]["watched_seconds"], 60)
        activity.mark_delivered(event_id)
        activity.mark_delivered(event_id)
        self.assertEqual(activity.pending_events(), [])
        self.assertEqual(activity.list_events()["items"][0]["attempts"], 2)

    def test_finished_with_zero_delta_is_immediate_and_deduplicated(self):
        self.enable()
        activity.record_watch(self.video, 60, 180, False)
        activity.record_watch(self.video, 0, 180, True)
        activity.record_watch(self.video, 0, 180, True)
        events = activity.pending_events()
        self.assertEqual(len(events), 2)
        self.assertTrue(events[-1]["payload"]["finished"])
        self.assertEqual(events[-1]["payload"]["status"], "finished")
        self.assertEqual(events[-1]["payload"]["watched_seconds"], 0)
        self.assertEqual(events[-1]["payload"]["total_watched_seconds"], 60)
        # A replay has another finish, even if its final report has no elapsed time.
        activity.record_watch(self.video, 4, 4, False)
        activity.record_watch(self.video, 0, 180, True)
        self.assertEqual(len(activity.pending_events()), 3)
        self.assertEqual(activity.pending_events()[-1]["payload"]["watched_seconds"], 4)

    def test_partial_visit_flushes_after_one_wall_clock_minute_and_restart(self):
        self.enable()
        with patch.object(activity, "_now", return_value="2026-09-24T12:00:00+00:00"):
            activity.record_watch(self.video, 12, 90, False)
        activity.init_db()
        with patch.object(activity, "_now", return_value="2026-09-24T12:00:59+00:00"):
            self.assertEqual(activity.flush_watches(), [])
        with patch.object(activity, "_now", return_value="2026-09-24T12:01:00+00:00"):
            self.assertEqual(len(activity.flush_watches()), 1)
        self.assertEqual(activity.pending_events()[0]["payload"]["watched_seconds"], 12)
        self.assertEqual(activity.flush_watches(force=True), [])

    def test_disabling_keeps_pending_work_without_flushing_or_adding_time(self):
        self.enable()
        activity.record_watch(self.video, 10, 10, False)
        activity.update_state(enabled=False)
        activity.record_watch(self.video, 10, 20, False)
        self.assertEqual(activity.flush_watches(force=True), [])
        self.enable()
        activity.flush_watches(force=True)
        self.assertEqual(activity.pending_events()[0]["payload"]["watched_seconds"], 10)

    def test_impressions_are_snapshot_based_and_paginate_from_the_beginning(self):
        self.enable()
        activity.record_search("birds", "library", 3, event_id="search-first")
        activity.record_impressions([self.video], {"filter": "unwatched"}, event_id="cards")
        self.video["title"] = "Changed afterwards"
        activity.record_impressions([self.video], "retry", event_id="cards")
        page = activity.list_events(limit=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["id"], "search-first")
        self.assertEqual(page["next_offset"], 1)
        impression = activity.list_events(event_type="recommendation_impressions")
        self.assertEqual(impression["items"][0]["payload"]["items"][0]["title"],
                         "Bird photography")
        self.assertIsNone(impression["next_offset"])

    def test_invalid_numbers_and_oversize_events_are_rejected(self):
        self.enable()
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                activity.record_watch(self.video, value, 0, False)
        with self.assertRaises(ValueError):
            activity.enqueue("bad", {"items": ["item"] * 201})
        with self.assertRaises(ValueError):
            activity.enqueue("bad", {"items": ["x" * 16000] * 17})
        activity.record_search("bird\x00song", "library", 3)
        self.assertEqual(activity.pending_events()[0]["payload"]["query"], "birdsong")


if __name__ == "__main__":
    unittest.main()
