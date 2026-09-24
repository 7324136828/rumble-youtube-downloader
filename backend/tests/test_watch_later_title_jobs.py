"""Offline HTTP/background-job tests for saved video title retrieval."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config
from app.routers.recommendations import router
from app.schemas.recommendation_providers import default_providers
from app.services import ai_title_lookup, db, page_link_import, video_title_lookup, watch_later_titles as titles

URL = "https://www.youtube.com/watch?v=abcdefghijk"


class WatchLaterTitleJobsTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(config, "JOBS_DB_PATH", Path(folder) / "catalog.db"))
        db.init_db()
        self.enterContext(patch("app.services.video_redirects.resolve_import", side_effect=lambda items, providers: items))
        self.enterContext(patch("app.services.watch_later_thumbnails.schedule_items", side_effect=lambda items: items))
        titles._PENDING.clear()
        self.addCleanup(titles._PENDING.clear)
        self.pool = self.enterContext(patch.object(titles, "_POOL"))
        self.lookup = self.enterContext(patch.object(video_title_lookup, "lookup_title", return_value="Fetched video title"))
        self.ai = self.enterContext(patch.object(ai_title_lookup, "lookup_title",
                                                 side_effect=ai_title_lookup.AiTitleError("AI title lookup is not enabled.")))
        app = FastAPI()
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def save(self, videos=None, **options):
        response = self.client.post("/api/recommendations/watch-later", json={
            "videos": videos if videos is not None else [{"source_url": URL}], **options})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_job(self, index=0):
        call = self.pool.submit.call_args_list[index]
        call.args[0](*call.args[1:])

    def test_save_returns_before_lookup_and_later_persists_title_with_ai_off(self):
        saved = self.save()
        item = saved["items"][0]
        self.assertFalse(db.get_recommendation_settings()["enabled"])
        self.assertEqual(item["title_fetch_status"], "pending")
        self.lookup.assert_not_called()
        self.run_job()
        fetched = self.client.get("/api/recommendations/watch-later").json()["items"][0]
        self.assertEqual(fetched["title"], "Fetched video title")
        self.assertEqual(fetched["title_fetch_status"], "ready")
        self.assertEqual(fetched["title_fetch_method"], "source")
        self.assertIsNone(fetched["user_title"])
        self.assertFalse(fetched["verified"])
        self.assertEqual(fetched["origins"], ["watch_later"])
        self.assertEqual(db.get_recommendation_settings()["revision"], saved["revision"] + 1)
        self.assertFalse(titles._PENDING)
        self.ai.assert_not_called()

    def test_badge_only_page_preview_schedules_title_lookup_when_saved(self):
        with patch.object(page_link_import, "_request", return_value=(200, None,
                f'<a href="{URL}"><span>1440p</span><span>CC</span></a>')):
            preview = self.client.post("/api/recommendations/watch-later/preview-links",
                                       json={"page_url": "https://example.com/list"}).json()
        link = preview["items"][0]
        self.assertTrue(link["is_video"])
        self.assertIsNone(link["title"])
        item = self.save([{"source_url": link["video_url"]}])["items"][0]
        self.assertEqual(item["title_fetch_status"], "pending")
        self.run_job()
        self.assertEqual(db.get_watch_later_item(item["catalog_id"])["title"], "Fetched video title")

    def test_enabled_ai_fallback_stores_labeled_unverified_title(self):
        db.update_recommendation_settings({"model_id": "titles-model", "allow_ai_title_lookup": True})
        self.lookup.side_effect = video_title_lookup.TitleLookupError("No source metadata")
        self.ai.side_effect = None
        self.ai.return_value = "AI video title"
        item = self.save()["items"][0]
        self.run_job()
        fetched = db.get_watch_later_item(item["catalog_id"])
        self.assertEqual(fetched["title"], "AI video title")
        self.assertEqual(fetched["title_fetch_method"], "ai")
        self.assertFalse(fetched["verified"])
        self.ai.assert_called_once()

    def test_supplied_and_already_known_titles_do_not_start_lookups(self):
        saved = self.save([{"source_url": URL, "title": "My own title"}])
        endpoint = f"/api/recommendations/watch-later/{saved['items'][0]['catalog_id']}/title"
        result = self.client.post(endpoint).json()
        self.assertFalse(result["queued"])
        self.assertEqual(result["item"]["title"], "My own title")
        self.pool.submit.assert_not_called()
        self.lookup.assert_not_called()

    def test_force_query_fetches_and_replaces_an_existing_bad_title(self):
        item = self.save([{"source_url": URL, "title": 'thl("omhmotk56f0",0);'}])["items"][0]
        response = self.client.post(
            f"/api/recommendations/watch-later/{item['catalog_id']}/title?force=true")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["queued"])
        self.run_job()
        fetched = db.get_watch_later_item(item["catalog_id"])
        self.assertEqual(fetched["title"], "Fetched video title")
        self.assertIsNone(fetched["user_title"])

    def test_failed_forced_fetch_preserves_existing_title_for_retry(self):
        self.lookup.side_effect = video_title_lookup.TitleLookupError("No source metadata")
        item = self.save([{"source_url": URL, "title": 'thl("omhmotk56f0",0);'}])["items"][0]
        self.client.post(f"/api/recommendations/watch-later/{item['catalog_id']}/title?force=true")
        self.run_job()
        fetched = db.get_watch_later_item(item["catalog_id"])
        self.assertEqual(fetched["title"], 'thl("omhmotk56f0",0);')
        self.assertEqual(fetched["title_fetch_status"], "unavailable")
        self.assertTrue(fetched["title_fetch_error"])

    def test_import_can_opt_out_and_existing_entries_can_fetch_explicitly(self):
        saved = self.save(fetch_titles=False)
        self.pool.submit.assert_not_called()
        item = saved["items"][0]
        response = self.client.post(f"/api/recommendations/watch-later/{item['catalog_id']}/title")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["queued"])
        self.run_job()
        self.assertEqual(db.get_watch_later_item(item["catalog_id"])["title"], "Fetched video title")

    def test_pending_requests_coalesce_and_canonical_imports_deduplicate(self):
        saved = self.save([{"source_url": URL}, {"source_url": "https://youtu.be/abcdefghijk?t=5"}])
        item = saved["items"][0]
        for _ in range(3):
            response = self.client.post(f"/api/recommendations/watch-later/{item['catalog_id']}/title")
            self.assertTrue(response.json()["queued"])
        self.assertEqual(saved["added"], 1)
        self.assertEqual(self.pool.submit.call_count, 1)

    def test_failure_keeps_video_and_supports_retry_without_exposing_upstream_details(self):
        saved = self.save()["items"][0]
        self.lookup.side_effect = RuntimeError("private upstream response")
        self.run_job()
        item = db.get_watch_later_item(saved["catalog_id"])
        self.assertEqual(item["title"], URL)
        self.assertEqual(item["title_fetch_status"], "unavailable")
        self.assertNotIn("private upstream", item["title_fetch_error"])
        self.lookup.side_effect = None
        self.client.post(f"/api/recommendations/watch-later/{item['catalog_id']}/title")
        self.run_job(1)
        self.assertEqual(db.get_watch_later_item(item["catalog_id"])["title_fetch_status"], "ready")

    def test_queue_capacity_keeps_all_saves_and_marks_overflow_retryable(self):
        videos = [{"source_url": f"https://www.youtube.com/watch?v={number:011d}"} for number in range(3)]
        with patch.object(titles, "MAX_PENDING", 2):
            saved = self.save(videos)
        self.assertEqual(saved["added"], 3)
        self.assertEqual([item["title_fetch_status"] for item in saved["items"]], ["pending", "pending", "unavailable"])
        self.assertIn("busy", saved["items"][-1]["title_fetch_error"])
        self.assertEqual(self.pool.submit.call_count, 2)

    def test_removal_cancels_queued_work_without_fetching_or_resurrecting(self):
        item = self.save()["items"][0]
        self.client.delete(f"/api/recommendations/watch-later/{item['catalog_id']}")
        self.run_job()
        self.lookup.assert_not_called()
        self.assertIsNone(db.get_watch_later_item(item["catalog_id"]))
        self.assertEqual(self.client.post(f"/api/recommendations/watch-later/{item['catalog_id']}/title").status_code, 404)

    def test_remove_and_readd_gets_a_new_job_old_completion_cannot_replace_it(self):
        item = self.save()["items"][0]
        self.client.delete(f"/api/recommendations/watch-later/{item['catalog_id']}")
        readded = self.save()["items"][0]
        self.assertEqual(readded["catalog_id"], item["catalog_id"])
        self.assertEqual(self.pool.submit.call_count, 2)
        self.lookup.return_value = "New title"
        self.run_job(0)
        self.lookup.assert_not_called()
        interim = db.get_watch_later_item(item["catalog_id"])
        self.assertEqual(interim["title"], URL)
        self.assertEqual(interim["title_fetch_status"], "pending")
        self.run_job(1)
        self.assertEqual(self.lookup.call_count, 1)
        self.assertEqual(db.get_watch_later_item(item["catalog_id"])["title"], "New title")

    def test_disabled_provider_allows_requested_lookup_but_removed_provider_does_not(self):
        db.update_recommendation_settings({"providers": [{**provider, "enabled": False} for provider in default_providers()]})
        item = self.save()["items"][0]
        self.assertEqual(item["title_fetch_status"], "pending")
        db.update_recommendation_settings({"providers": []})
        self.run_job()
        self.lookup.assert_not_called()
        self.assertEqual(db.get_watch_later_item(item["catalog_id"])["title_fetch_status"], "unavailable")
        response = self.client.post(f"/api/recommendations/watch-later/{item['catalog_id']}/title")
        self.assertEqual(response.status_code, 400)

    def test_deadline_does_not_commit_late_title(self):
        item = self.save()["items"][0]
        now = [100.0]
        def late(*args):
            now[0] += titles.LOOKUP_TIMEOUT + 1
            return "Late title"
        self.lookup.side_effect = late
        with patch.object(titles, "time", SimpleNamespace(monotonic=lambda: now[0])):
            self.run_job()
        current = db.get_watch_later_item(item["catalog_id"])
        self.assertEqual(current["title"], URL)
        self.assertEqual(current["title_fetch_status"], "unavailable")
        self.assertIn("timed out", current["title_fetch_error"])

    def test_unavailable_worker_does_not_fail_save_and_controls_are_strict(self):
        self.pool.submit.side_effect = RuntimeError("executor shut down")
        item = self.save()["items"][0]
        self.assertEqual(item["title_fetch_status"], "unavailable")
        self.assertFalse(titles._PENDING)
        for value in (None, "true", 1):
            response = self.client.post("/api/recommendations/watch-later", json={"videos": [{"source_url": URL}], "fetch_titles": value})
            self.assertEqual(response.status_code, 422)

    def test_shutdown_cancels_queued_work_and_late_worker_cannot_fetch(self):
        item = self.save()["items"][0]
        titles.shutdown()
        self.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        self.assertFalse(titles._PENDING)
        self.run_job()
        self.lookup.assert_not_called()
        self.assertEqual(db.get_watch_later_item(item["catalog_id"])["title_fetch_status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
