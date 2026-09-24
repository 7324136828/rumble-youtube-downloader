"""Persistent link history, repeat filtering, and human overrides."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import db


def video(number=1):
    return {"id": f"vimeo.com:{number}", "source_url": f"https://vimeo.com/{number}",
            "connector": "vimeo.com", "title": f"Video {number}"}


class LinkSettingsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.folder / "jobs.db"))
        db.init_db()
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_link_stays_visible_until_user_marks_it_repeated(self):
        first = db.filter_presented_links([video()], "search-one")
        saved = db.record_presented_links(first, "search-one")
        self.assertEqual(saved[0]["link_id"], 1)
        self.assertEqual(len(db.filter_presented_links([video()], "search-one")), 1)
        self.assertEqual(len(db.filter_presented_links([video()], "search-two")), 1)
        repeated = self.client.patch(f"/api/settings/links/{saved[0]['link_id']}",
                                     json={"state": "silenced"})
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(db.filter_presented_links([video()], "search-three"), [])
        response = self.client.get("/api/settings/links", params={"state": "silenced"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["hide_repeated_links"])
        self.assertEqual(response.json()["items"][0]["state"], "silenced")
        self.assertEqual(response.json()["items"][0]["seen_count"], 1)

    def test_global_toggle_and_per_link_allow_or_exclude(self):
        saved = db.record_presented_links([video()], "one")[0]
        self.assertEqual(len(db.filter_presented_links([video()], "two")), 1)
        self.client.patch(f"/api/settings/links/{saved['link_id']}", json={"state": "silenced"})
        self.assertEqual(db.filter_presented_links([video()], "repeated"), [])
        allowed = self.client.patch(f"/api/settings/links/{saved['link_id']}",
                                    json={"state": "allowed"})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(len(db.filter_presented_links([video()], "three")), 1)
        excluded = self.client.patch(f"/api/settings/links/{saved['link_id']}",
                                     json={"state": "excluded"})
        self.assertEqual(excluded.status_code, 200)
        self.assertEqual(db.filter_presented_links([video()], "four"), [])
        setting = self.client.patch("/api/settings/links", json={"hide_repeated_links": False})
        self.assertEqual(setting.json(), {"hide_repeated_links": False})
        self.assertEqual(db.filter_presented_links([video()], "five"), [])

    def test_disabling_repeat_filter_releases_user_marked_repeat(self):
        saved = db.record_presented_links([video()], "one")[0]
        db.update_link_state(saved["link_id"], "silenced")
        self.assertEqual(db.filter_presented_links([video()], "two"), [])
        db.update_link_settings({"hide_repeated_links": False})
        self.assertEqual(len(db.filter_presented_links([video()], "three")), 1)

    def test_user_can_clear_a_marking_or_delete_the_record(self):
        saved = db.record_presented_links([video()], "one")[0]
        marked = self.client.patch(f"/api/settings/links/{saved['link_id']}",
                                   json={"state": "excluded"})
        self.assertEqual(marked.status_code, 200)
        cleared = self.client.patch(f"/api/settings/links/{saved['link_id']}",
                                    json={"state": "active"})
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["state"], "active")
        deleted = self.client.delete(f"/api/settings/links/{saved['link_id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True, "id": saved["link_id"]})
        self.assertEqual(self.client.get("/api/settings/links").json()["items"], [])
        # Deleting a record does not permanently classify its URL. A later
        # presentation records it again as neutral and unreviewed.
        rerecorded = db.record_presented_links([video()], "two")[0]
        self.assertEqual(db.list_link_history()["items"][0]["state"], "active")
        self.assertNotEqual(rerecorded["link_id"], saved["link_id"])
        self.assertEqual(self.client.delete(
            f"/api/settings/links/{saved['link_id']}").status_code, 404)

    def test_validation_and_missing_links_are_bounded(self):
        self.assertEqual(self.client.get("/api/settings/links", params={"state": "private"}).status_code, 400)
        self.assertEqual(self.client.patch("/api/settings/links", json={"hide_repeated_links": "yes"}).status_code, 422)
        self.assertEqual(self.client.patch("/api/settings/links/999", json={"state": "allowed"}).status_code, 404)
        self.assertEqual(self.client.patch("/api/settings/links/1", json={"state": "unknown"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
