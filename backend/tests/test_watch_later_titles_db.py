"""Durable title lookup claims preserve manual membership and metadata ownership."""
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.schemas.recommendation_providers import default_providers
from app.services import db

URL = "https://www.youtube.com/watch?v=abcdefghijk"


class WatchLaterTitleDatabaseTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "jobs.db"
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.path))
        db.init_db()
        self.providers = default_providers()

    def save(self, **fields):
        return db.add_watch_later([{"source_url": URL, **fields}])["items"][0]

    def row(self, catalog_id):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            return dict(connection.execute("SELECT * FROM recommendation_videos WHERE id=?", (catalog_id,)).fetchone())

    def revision(self):
        return db.get_recommendation_settings()["revision"]

    def test_claim_success_preserves_provenance_and_counts_and_hides_token(self):
        item = self.save(description="My note")
        catalog_id = item["catalog_id"]
        db.record_recommendations([item], self.providers, fallback=True)
        before = self.row(catalog_id)
        revision = self.revision()
        pending = db.begin_watch_later_title(catalog_id, "first-token")
        self.assertEqual(pending["title_fetch_status"], "pending")
        self.assertIsNone(pending["title_fetch_error"])
        self.assertEqual(self.revision(), revision)
        for payload in (pending, db.get_watch_later_item(catalog_id), db.list_watch_later()["items"][0],
                        db.list_catalog_candidates(self.providers)[0]):
            self.assertNotIn("title_fetch_token", payload)
        self.assertTrue(db.finish_watch_later_title(catalog_id, "first-token", "  Actual video title  "))
        result = db.get_watch_later_item(catalog_id)
        self.assertEqual(result["title"], "Actual video title")
        self.assertEqual(result["title_fetch_status"], "ready")
        self.assertIsNone(result["title_fetch_error"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["verification"], "user_added")
        self.assertEqual(result["origins"], ["watch_later"])
        self.assertEqual(result["description"], "My note")
        self.assertEqual(self.revision(), revision + 1)
        after = self.row(catalog_id)
        self.assertIsNone(after["title_fetch_token"])
        changed_fields = {"title", "updated_at", "title_fetch_status", "title_fetch_token", "title_fetch_error"}
        self.assertEqual({key: value for key, value in before.items() if key not in changed_fields},
                         {key: value for key, value in after.items() if key not in changed_fields})
        self.assertFalse(db.finish_watch_later_title(catalog_id, "first-token", "Duplicate completion"))
        self.assertEqual(self.revision(), revision + 1)

    def test_failure_retry_and_status_changes_do_not_bump_revision(self):
        catalog_id = self.save()["catalog_id"]
        revision = self.revision()
        db.begin_watch_later_title(catalog_id, "failed")
        self.assertTrue(db.finish_watch_later_title(catalog_id, "failed", error="  Website unavailable  "))
        failed = db.get_watch_later_item(catalog_id)
        self.assertEqual(failed["title_fetch_status"], "unavailable")
        self.assertEqual(failed["title_fetch_error"], "Website unavailable")
        self.assertEqual(failed["title"], URL)
        self.assertEqual(self.revision(), revision)
        retry = db.begin_watch_later_title(catalog_id, "retry")
        self.assertEqual(retry["title_fetch_status"], "pending")
        self.assertIsNone(retry["title_fetch_error"])
        self.assertTrue(db.finish_watch_later_title(catalog_id, "retry", title=" \n ", error="e" * 600))
        self.assertEqual(len(db.get_watch_later_item(catalog_id)["title_fetch_error"]), 500)
        self.assertEqual(self.revision(), revision)

    def test_missing_removed_and_existing_titles_cannot_be_claimed(self):
        self.assertIsNone(db.get_watch_later_item(1))
        self.assertIsNone(db.begin_watch_later_title(1, "missing"))
        self.assertFalse(db.finish_watch_later_title(1, "missing", "No record"))
        catalog_id = self.save(title="My own title")["catalog_id"]
        self.assertIsNone(db.begin_watch_later_title(catalog_id, "manual"))
        self.save(title=None)
        db.store_discoveries([{"source_url": URL, "title": "Known provider title", "verified": True,
                              "verification": "provider_search"}], self.providers)
        self.assertIsNone(db.begin_watch_later_title(catalog_id, "known"))
        db.remove_watch_later(catalog_id)
        self.assertIsNone(db.get_watch_later_item(catalog_id))
        self.assertIsNone(db.begin_watch_later_title(catalog_id, "removed"))

    def test_forced_lookup_replaces_bad_manual_title_only_after_success(self):
        catalog_id = self.save(title='thl("omhmotk56f0",0);')["catalog_id"]
        self.assertIsNotNone(db.begin_watch_later_title(catalog_id, "failed-refresh", force=True))
        self.assertTrue(db.finish_watch_later_title(catalog_id, "failed-refresh", error="Unavailable", force=True))
        failed = db.get_watch_later_item(catalog_id)
        self.assertEqual(failed["title"], 'thl("omhmotk56f0",0);')
        self.assertEqual(failed["user_title"], 'thl("omhmotk56f0",0);')
        self.assertEqual(failed["title_fetch_status"], "unavailable")
        self.assertEqual(failed["title_fetch_error"], "Unavailable")
        revision = self.revision()
        self.assertIsNotNone(db.begin_watch_later_title(catalog_id, "successful-refresh", force=True))
        self.assertTrue(db.finish_watch_later_title(catalog_id, "successful-refresh", title="Actual title",
                                                    method="source", force=True,
                                                    expected_title='thl("omhmotk56f0",0);'))
        refreshed = db.get_watch_later_item(catalog_id)
        self.assertEqual(refreshed["title"], "Actual title")
        self.assertIsNone(refreshed["user_title"])
        self.assertEqual(refreshed["title_fetch_status"], "ready")
        self.assertEqual(refreshed["title_fetch_method"], "source")
        self.assertEqual(self.revision(), revision + 1)

    def test_new_manual_title_wins_over_late_forced_lookup(self):
        catalog_id = self.save(title='thl("omhmotk56f0",0);')["catalog_id"]
        claimed = db.begin_watch_later_title(catalog_id, "forced", force=True)
        self.save(title="My corrected title")
        self.assertTrue(db.finish_watch_later_title(catalog_id, "forced", title="Fetched title",
                                                    method="source", force=True,
                                                    expected_title=claimed["title"]))
        item = db.get_watch_later_item(catalog_id)
        self.assertEqual(item["title"], "My corrected title")
        self.assertEqual(item["user_title"], "My corrected title")
        self.assertEqual(self.row(catalog_id)["title"], "Fetched title")

    def test_disabled_configured_website_is_allowed_removed_website_is_not(self):
        catalog_id = self.save()["catalog_id"]
        disabled = [{**provider, "enabled": False} for provider in self.providers]
        db.update_recommendation_settings({"providers": disabled})
        self.assertIsNotNone(db.begin_watch_later_title(catalog_id, "disabled-allowed"))
        db.finish_watch_later_title(catalog_id, "disabled-allowed", error="Unavailable")
        db.update_recommendation_settings({"providers": []})
        self.assertIsNone(db.begin_watch_later_title(catalog_id, "removed-provider"))
        self.assertIsNotNone(db.get_watch_later_item(catalog_id))

    def test_manual_title_imported_while_pending_remains_authoritative(self):
        catalog_id = self.save()["catalog_id"]
        db.begin_watch_later_title(catalog_id, "in-flight")
        self.save(title="My updated title", description="My new note")
        revision = self.revision()
        self.assertTrue(db.finish_watch_later_title(catalog_id, "in-flight", title="Retrieved provider title"))
        item = db.get_watch_later_item(catalog_id)
        self.assertEqual(item["title"], "My updated title")
        self.assertEqual(item["user_title"], "My updated title")
        self.assertEqual(item["description"], "My new note")
        self.assertEqual(item["title_fetch_status"], "ready")
        self.assertEqual(self.row(catalog_id)["title"], "Retrieved provider title")
        self.assertEqual(self.revision(), revision + 1)

    def test_saved_item_exposes_matching_ready_media_for_direct_playback(self):
        catalog_id = self.save(title="Ready video")["catalog_id"]
        self.assertIsNone(db.get_watch_later_item(catalog_id)["media_id"])
        db.create_video("ready-media", URL, "youtube", "best", self.path.parent / "ready-media")
        db.update_video("ready-media", status="ready")
        self.assertEqual(db.get_watch_later_item(catalog_id)["media_id"], "ready-media")

    def test_new_discovery_during_lookup_wins_without_revision_or_provenance_change(self):
        catalog_id = self.save()["catalog_id"]
        db.begin_watch_later_title(catalog_id, "in-flight")
        db.store_discoveries([{"source_url": URL, "title": "Independent verified title", "description": "Metadata",
                              "verified": True, "verification": "provider_search", "duration": 60}], self.providers)
        revision = self.revision()
        before = self.row(catalog_id)
        self.assertTrue(db.finish_watch_later_title(catalog_id, "in-flight", "Other page title", error="Old error"))
        item = db.get_watch_later_item(catalog_id)
        self.assertEqual(item["title"], "Independent verified title")
        self.assertTrue(item["verified"])
        self.assertEqual(item["verification"], "provider_search")
        self.assertEqual(set(item["origins"]), {"custom_search", "watch_later"})
        self.assertEqual(item["duration"], 60)
        self.assertEqual(item["title_fetch_status"], "ready")
        self.assertIsNone(item["title_fetch_error"])
        self.assertEqual(self.revision(), revision)
        self.assertEqual(self.row(catalog_id)["updated_at"], before["updated_at"])

    def test_removal_and_readdition_cannot_receive_old_lookup_completion(self):
        catalog_id = self.save()["catalog_id"]
        db.begin_watch_later_title(catalog_id, "old")
        db.remove_watch_later(catalog_id)
        self.assertEqual(self.row(catalog_id)["title_fetch_status"], "idle")
        self.assertIsNone(self.row(catalog_id)["title_fetch_token"])
        self.assertFalse(db.finish_watch_later_title(catalog_id, "old", "Removed result"))
        self.assertEqual(self.save()["catalog_id"], catalog_id)
        revision = self.revision()
        self.assertFalse(db.finish_watch_later_title(catalog_id, "old", "Late old result"))
        db.begin_watch_later_title(catalog_id, "new")
        self.assertFalse(db.finish_watch_later_title(catalog_id, "old", "Out of order result"))
        self.assertEqual(self.revision(), revision)
        self.assertTrue(db.finish_watch_later_title(catalog_id, "new", "New result"))
        self.assertEqual(db.get_watch_later_item(catalog_id)["title"], "New result")

    def test_only_one_concurrent_claim_can_start_and_wrong_token_cannot_finish(self):
        catalog_id = self.save()["catalog_id"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda token: db.begin_watch_later_title(catalog_id, token), ["one", "two"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        active = self.row(catalog_id)["title_fetch_token"]
        inactive = "two" if active == "one" else "one"
        self.assertFalse(db.finish_watch_later_title(catalog_id, inactive, "Wrong owner"))
        self.assertTrue(db.finish_watch_later_title(catalog_id, active, "Correct owner"))

    def test_internal_token_filtered_read_only_returns_current_pending_membership(self):
        catalog_id = self.save()["catalog_id"]
        self.assertIsNone(db.get_watch_later_item(catalog_id, title_fetch_token="old"))
        db.begin_watch_later_title(catalog_id, "old")
        owned = db.get_watch_later_item(catalog_id, title_fetch_token="old")
        self.assertEqual(owned["title_fetch_status"], "pending")
        self.assertNotIn("title_fetch_token", owned)
        self.assertIsNone(db.get_watch_later_item(catalog_id, title_fetch_token="other"))
        db.remove_watch_later(catalog_id)
        self.assertIsNone(db.get_watch_later_item(catalog_id, title_fetch_token="old"))
        self.save()
        db.begin_watch_later_title(catalog_id, "new")
        self.assertIsNone(db.get_watch_later_item(catalog_id, title_fetch_token="old"))
        self.assertIsNotNone(db.get_watch_later_item(catalog_id, title_fetch_token="new"))
        db.finish_watch_later_title(catalog_id, "new", "Current title")
        self.assertIsNone(db.get_watch_later_item(catalog_id, title_fetch_token="new"))
        self.assertEqual(db.get_watch_later_item(catalog_id)["title"], "Current title")

    def test_restart_retires_pending_token_without_changing_settings_revision(self):
        catalog_id = self.save()["catalog_id"]
        db.begin_watch_later_title(catalog_id, "before-restart")
        revision = self.revision()
        db.init_db()
        item = db.get_watch_later_item(catalog_id)
        self.assertEqual(item["title_fetch_status"], "idle")
        self.assertIsNone(item["title_fetch_error"])
        self.assertIsNone(self.row(catalog_id)["title_fetch_token"])
        self.assertEqual(self.revision(), revision)
        self.assertFalse(db.finish_watch_later_title(catalog_id, "before-restart", "Old worker"))
        db.begin_watch_later_title(catalog_id, "after-restart")
        db.finish_watch_later_title(catalog_id, "after-restart", "New worker")
        db.init_db()
        self.assertEqual(db.get_watch_later_item(catalog_id)["title_fetch_status"], "ready")

    def test_legacy_catalog_migrates_without_losing_data(self):
        legacy = self.path.with_name("legacy.db")
        legacy_schema = db._SCHEMA.replace("    title_fetch_status TEXT NOT NULL DEFAULT 'idle',\n", "").replace(
            "    title_fetch_error TEXT,\n", "").replace("    title_fetch_token TEXT,\n", "").replace(
            "    thumbnail_path TEXT,\n", "").replace(
            "    thumbnail_fetch_status TEXT NOT NULL DEFAULT 'idle',\n", "").replace(
            "    thumbnail_fetch_error TEXT,\n", "").replace("    thumbnail_fetch_token TEXT,\n", "")
        with closing(sqlite3.connect(legacy)) as connection:
            connection.executescript(legacy_schema)
            connection.execute("INSERT INTO recommendation_websites (id,domain,name,enabled,created_at,updated_at)"
                               " VALUES ('youtube','youtube.com','YouTube',1,'old','old')")
            connection.execute("INSERT INTO recommendation_videos (id,website_id,source_url,provider_video_id,title,user_title,"
                               " verified,discovered_count,recommended_count,created_at,updated_at)"
                               " VALUES (1,'youtube',?,'youtube:abcdefghijk','Original','Personal',1,3,7,'old','old')", (URL,))
            connection.execute("INSERT INTO recommendation_video_origins (video_id,origin,first_seen_at,last_seen_at)"
                               " VALUES (1,'watch_later','old','old')")
            connection.execute("UPDATE recommendation_settings SET revision=12")
            connection.commit()
        with patch.object(config, "JOBS_DB_PATH", legacy):
            db.init_db()
            item = db.get_watch_later_item(1)
            self.assertEqual(item["title_fetch_status"], "idle")
            self.assertIsNone(item["title_fetch_error"])
            self.assertEqual(item["thumbnail_fetch_status"], "idle")
            self.assertIsNone(item["thumbnail_fetch_error"])
            self.assertNotIn("title_fetch_token", item)
            self.assertEqual(item["title"], "Personal")
            self.assertTrue(item["verified"])
            self.assertEqual((item["discovered_count"], item["recommended_count"]), (3, 7))
            self.assertEqual(self.revision(), 12)


if __name__ == "__main__":
    unittest.main()
