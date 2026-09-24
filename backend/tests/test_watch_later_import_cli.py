"""The standalone import helper validates everything before sending bounded batches."""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_watch_later.py"
spec = importlib.util.spec_from_file_location("watch_later_cli", SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def video(index=1):
    return {"source_url": f"https://vimeo.com/{index}", "title": "Nature", "description": "Birds by the water"}


def response(added, updated=0):
    reply = MagicMock()
    reply.__enter__.return_value.read.return_value = json.dumps({"added": added, "updated": updated}).encode()
    return reply


class WatchLaterImportCliTest(unittest.TestCase):
    def invoke(self, payload, *args):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["-", *args], stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_file_or_stdin_checks_format_without_contacting_api(self):
        with patch.object(cli, "build_opener") as network:
            code, output, error = self.invoke({"videos": [video()]}, "--dry-run")
            self.assertEqual((code, error), (0, ""))
            self.assertIn("1 videos in 1 batch", output)
            with tempfile.TemporaryDirectory() as folder:
                filename = Path(folder) / "saved.json"
                filename.write_text(json.dumps([video()]), encoding="utf-8-sig")
                self.assertEqual(cli.main([str(filename), "--dry-run"], stdout=io.StringIO()), 0)
        network.assert_not_called()

    def test_input_fields_lengths_unknown_keys_and_total_count_are_rejected(self):
        cases = [[], {}, {"videos": [video()], "verified": True}, [None], ["url"],
                 [{**video(), "verified": True}], [{**video(), "origins": ["provider_search"]}],
                 [{**video(), "title": 5}], [{**video(), "title": "\ud800"}], [{**video(), "source_url": None}],
                 [{**video(), "source_url": "  "}], [{**video(), "source_url": "x" * 2049}],
                 [{**video(), "title": "x" * 501}], [{**video(), "description": "x" * 10001}],
                 [video()] * (cli.MAX_VIDEOS + 1)]
        with patch.object(cli, "build_opener") as network:
            for payload in cases:
                with self.subTest(payload=str(payload)[:80]):
                    code, _, error = self.invoke(payload)
                    self.assertEqual(code, 1)
                    self.assertIn("Import failed", error)
        network.assert_not_called()

    def test_chunks_at_two_hundred_and_sends_only_supported_metadata(self):
        with patch.object(cli, "build_opener") as factory:
            opener = factory.return_value
            opener.open.side_effect = [response(198, 2), response(1)]
            code, output, error = self.invoke([video(index) for index in range(201)], "--api", "http://localhost:8123/")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("199 added, 2 updated", output)
        self.assertEqual(opener.open.call_count, 2)
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual([len(json.loads(request.data)["videos"]) for request in requests], [200, 1])
        for request in requests:
            self.assertEqual(request.full_url, "http://localhost:8123/api/recommendations/watch-later")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("Content-type"), "application/json")
            self.assertEqual(set(json.loads(request.data)["videos"][0]), {"source_url", "title", "description"})

    def test_failed_batch_reports_confirmed_progress_without_echoing_validation_input(self):
        detail = {"detail": [{"msg": "Invalid website\u001b", "input": "secret raw input", "loc": ["body"]}]}
        error = HTTPError("http://localhost", 422, "Unprocessable", {}, io.BytesIO(json.dumps(detail).encode()))
        with patch.object(cli, "build_opener") as factory:
            factory.return_value.open.side_effect = [response(200), error]
            code, _, output = self.invoke([video(index) for index in range(401)])
        self.assertEqual(code, 1)
        self.assertIn("batch 2/3", output)
        self.assertIn("200 added, 0 updated (200 input records)", output)
        self.assertIn("HTTP 422: Invalid website", output)
        self.assertNotIn("secret raw input", output)
        self.assertNotIn("\u001b", output)
        self.assertEqual(factory.return_value.open.call_count, 2)

    def test_title_lookup_can_be_disabled_for_script_imports(self):
        with patch.object(cli, "build_opener") as factory:
            factory.return_value.open.return_value = response(1)
            code, _, error = self.invoke([{"source_url": video()["source_url"]}], "--no-fetch-titles")
        self.assertEqual((code, error), (0, ""))
        payload = json.loads(factory.return_value.open.call_args.args[0].data)
        self.assertIs(payload["fetch_titles"], False)

    def test_redirect_resolution_can_be_disabled_for_script_imports(self):
        with patch.object(cli, "build_opener") as factory:
            factory.return_value.open.return_value = response(1)
            code, _, error = self.invoke([{"source_url": video()["source_url"]}], "--no-resolve-redirects")
        self.assertEqual((code, error), (0, ""))
        payload = json.loads(factory.return_value.open.call_args.args[0].data)
        self.assertIs(payload["resolve_redirects"], False)

    def test_invalid_json_size_duplicate_keys_and_api_url_make_no_requests(self):
        with patch.object(cli, "build_opener") as factory:
            for data in ("not JSON", '[{"source_url":"a","source_url":"b"}]'):
                self.assertEqual(cli.main(["-"], stdin=io.StringIO(data), stderr=io.StringIO()), 1)
            with patch.object(cli, "MAX_INPUT_BYTES", 10):
                self.assertEqual(self.invoke([video()])[0], 1)
            for api in ("file:///private", "http://user:password@localhost", "http://localhost/#fragment"):
                self.assertEqual(self.invoke([video()], "--api", api)[0], 1)
        factory.assert_not_called()

    def test_unknown_success_counts_fail_without_claiming_saved_rows(self):
        with patch.object(cli, "build_opener") as factory:
            factory.return_value.open.return_value = response(2)
            code, _, error = self.invoke([video()])
        self.assertEqual(code, 1)
        self.assertIn("did not confirm valid import counts", error)
        self.assertIn("0 added, 0 updated", error)


if __name__ == "__main__":
    unittest.main()
