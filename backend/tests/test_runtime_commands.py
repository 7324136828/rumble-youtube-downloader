"""Portable command dispatch keeps metadata work in bounded child processes."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import pipeline, recommendation_tools, search
from app.utils.runtime import ytdlp_command


class RuntimeCommandsTest(unittest.TestCase):
    def test_python_and_frozen_commands_use_enabled_javascript_runtime(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "frozen", False, create=True):
            self.assertEqual(ytdlp_command(), [sys.executable, "-m", "yt_dlp",
                                             "--js-runtimes", "node", "--js-runtimes", "deno"])
        runtime = "node:C:/portable/bin/node.exe"
        with patch.dict(os.environ, {"YTDLP_JS_RUNTIME": runtime}), patch.object(sys, "frozen", True, create=True):
            self.assertEqual(ytdlp_command(), [sys.executable, "--yt-dlp", "--js-runtimes", runtime])

    def test_frozen_youtube_search_keeps_deadline_and_flat_metadata_flags(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"entries": []}), "")
        with patch.object(sys, "frozen", True, create=True), patch.object(search.subprocess, "run", return_value=result) as child:
            self.assertEqual(search.search_youtube("nature", 3), [])
        command = child.call_args.args[0]
        self.assertEqual(command[:2], [sys.executable, "--yt-dlp"])
        self.assertIn("--flat-playlist", command)
        self.assertIn("--skip-download", command)
        self.assertEqual(command[-2:], ["--", "ytsearch3:nature"])
        self.assertEqual(child.call_args.kwargs["timeout"], search.SEARCH_TIMEOUT)

    def test_frozen_rumble_metadata_preserves_impersonation_options(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"id": "v123abc", "title": "Nature"}), "")
        with patch.object(sys, "frozen", True, create=True), patch.object(recommendation_tools.subprocess, "run", return_value=result) as child:
            recommendation_tools.extract_video_metadata("https://rumble.com/v123abc-nature.html")
        command = child.call_args.args[0]
        self.assertEqual(command[:2], [sys.executable, "--yt-dlp"])
        self.assertLess(command.index("--impersonate"), command.index("--"))
        self.assertEqual(command[command.index("--impersonate") + 1], "chrome")
        self.assertIn("--skip-download", command)

    def test_frozen_legacy_command_uses_script_dispatch(self):
        with patch.object(sys, "frozen", True, create=True):
            command = pipeline.build_command(Path("job"), {"no_transcript": True})
        self.assertEqual(command[:2], [sys.executable, "--convert-script"])
        self.assertIn("--no-transcript", command)


if __name__ == "__main__":
    unittest.main()
