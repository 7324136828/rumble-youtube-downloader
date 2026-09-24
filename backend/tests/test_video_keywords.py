"""AI video keyword generation, persistence, and local search tests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import connector_client, db, video_keywords


class VideoKeywordsTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.database = Path(folder) / "jobs.db"
        self.enterContext(patch.object(config, "JOBS_DB_PATH", self.database))
        db.init_db()
        self.video_id = "keyword-video"
        db.create_video(self.video_id, "https://example.com/video", "generic", "best",
                        Path(folder) / "media")
        db.complete_video(
            self.video_id, "2026-09-24T00:00:00+00:00", progress=100,
            title="Night photography in the city",
            description="A practical guide to long exposures, tripods, and urban light trails.",
            uploader="Example creator", duration=240,
        )

    def enable(self):
        db.update_recommendation_settings({"model_id": "keyword-model", "enabled": True})

    def test_model_receives_title_and_description_and_keywords_are_searchable(self):
        self.enable()
        message = {"role": "assistant", "content": json.dumps({
            "keywords": ["Night Photography", "long exposure", "City Lights",
                         "tripod", "night photography"]})}
        with patch.object(connector_client, "complete", return_value=message) as complete:
            result = video_keywords.generate(self.video_id)

        self.assertEqual(result, ["night photography", "long exposure", "city lights", "tripod"])
        prompt = json.loads(complete.call_args.args[1][1]["content"])
        self.assertEqual(prompt["title"], "Night photography in the city")
        self.assertIn("urban light trails", prompt["description"])
        self.assertEqual(db.get_video_keywords(self.video_id), result)

        catalog = db.video_keyword_catalog("exposure")
        self.assertEqual([item["id"] for item in catalog["videos"]], [self.video_id])
        self.assertIn({"keyword": "long exposure", "count": 1}, catalog["keywords"])
        self.assertEqual(catalog["status"]["ready"], 1)

    def test_disabled_recommendations_do_not_contact_the_model(self):
        with patch.object(connector_client, "complete") as complete:
            self.assertIsNone(video_keywords.generate(self.video_id))
        complete.assert_not_called()
        self.assertEqual(db.get_video_keywords(self.video_id), [])

    def test_invalid_model_output_is_not_saved(self):
        self.enable()
        with patch.object(connector_client, "complete", return_value={
                "role": "assistant", "content": "not json"}):
            self.assertIsNone(video_keywords.generate(self.video_id))
        self.assertEqual(db.get_video_keywords(self.video_id), [])
        self.assertEqual(db.video_keyword_catalog()["status"]["failed"], 1)

    def test_keyword_api_returns_media_payload_and_delete_cascades(self):
        self.enable()
        db.begin_video_keyword_generation(self.video_id, "keyword-model")
        db.finish_video_keyword_generation(self.video_id, ["photography", "city lights"])
        with TestClient(app) as client:
            response = client.get("/api/video-keywords?q=photography")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["videos"][0]["id"], self.video_id)
        self.assertEqual(payload["videos"][0]["keywords"], ["photography", "city lights"])
        self.assertEqual(payload["videos"][0]["stream_url"],
                         f"/api/media/{self.video_id}/stream")

        db.delete_video(self.video_id)
        self.assertEqual(db.video_keyword_catalog()["videos"], [])


if __name__ == "__main__":
    unittest.main()
