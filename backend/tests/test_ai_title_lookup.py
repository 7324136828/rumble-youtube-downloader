"""Strict Connector title fallback behavior."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JOBS_DB_PATH", str(Path(tempfile.mkdtemp()) / "test_jobs.db"))

from app.services import ai_title_lookup, connector_client

PROVIDER = {"id": "vimeo.com", "name": "Vimeo", "domain": "vimeo.com", "enabled": True}
SETTINGS = {"allow_ai_title_lookup": True, "model_id": "titles-model"}


class AiTitleLookupTest(unittest.TestCase):
    def test_returns_sanitized_exact_json_title(self):
        message = {"role": "assistant", "content": '```json\n{"title":"  Birds   over water  "}\n```'}
        with patch.object(connector_client, "complete", return_value=message) as complete:
            title = ai_title_lookup.lookup_title("https://vimeo.com/123", PROVIDER, SETTINGS)
        self.assertEqual(title, "Birds over water")
        self.assertEqual(complete.call_args.args[0], "titles-model")
        self.assertEqual(complete.call_args.kwargs["timeout"], 25)
        prompt = json.loads(complete.call_args.args[1][1]["content"])
        self.assertEqual(prompt, {"website": "Vimeo", "url": "https://vimeo.com/123"})

    def test_disabled_missing_or_null_titles_never_call_model_or_guess(self):
        with patch.object(connector_client, "complete") as complete:
            for settings in ({}, {"allow_ai_title_lookup": True, "model_id": None}):
                with self.subTest(settings=settings), self.assertRaises(ai_title_lookup.AiTitleError):
                    ai_title_lookup.lookup_title("https://vimeo.com/123", PROVIDER, settings)
        complete.assert_not_called()
        for content in ('{"title":null}', '{"title":"Sign in"}', '{"title":"1440pCC"}',
                        '{"title":"Video"}', '{"title":"x","extra":1}', "not json"):
            with self.subTest(content=content), patch.object(connector_client, "complete",
                    return_value={"role": "assistant", "content": content}), self.assertRaises(ai_title_lookup.AiTitleError):
                ai_title_lookup.lookup_title("https://vimeo.com/123", PROVIDER, SETTINGS)

    def test_connector_errors_are_sanitized_and_guard_runs(self):
        calls = []
        with patch.object(connector_client, "complete", side_effect=connector_client.ConnectorError("private")), \
                self.assertRaisesRegex(ai_title_lookup.AiTitleError, "could not fetch"):
            ai_title_lookup.lookup_title("https://vimeo.com/123", PROVIDER, SETTINGS, lambda: calls.append(True))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
