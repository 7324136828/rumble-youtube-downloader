"""Offline unit tests for the backend job layer."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("JOBS_DB_PATH",
                      str(Path(tempfile.mkdtemp()) / "test_jobs.db"))

from app import config  # noqa: E402
from app.services import db, pipeline  # noqa: E402
from app.utils import temp_manager  # noqa: E402


class TempManagerTest(unittest.TestCase):
    def test_job_dir_is_under_system_temp(self):
        job_dir = temp_manager.create_job_dir("test-job-1")
        self.assertTrue(str(job_dir).startswith(
            str(temp_manager.jobs_root())))
        for sub in ("inputs", "work", "outputs", "archive"):
            self.assertTrue((job_dir / sub).is_dir())
        temp_manager.purge_job_dir(job_dir)
        self.assertFalse(job_dir.exists())


class JobStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_job_lifecycle(self):
        job = db.create_job("job-a", "2 URLs", 42, "tmp/job-a",
                            ["https://rumble.com/a.html"], {"model": "turbo"})
        self.assertEqual(job["status"], "queued")
        db.update_job("job-a", status="in_progress", progress=40)
        job = db.get_job("job-a")
        self.assertEqual(job["status"], "in_progress")
        self.assertEqual(job["progress"], 40)
        db.update_job("job-a", status="completed", progress=100,
                      zip_path="tmp/job-a/archive/x.zip")
        self.assertEqual(db.get_job("job-a")["status"], "completed")
        self.assertTrue(any(j["id"] == "job-a" for j in db.list_jobs()))

    def test_params_round_trip(self):
        db.create_job("job-b", "u", 1, "t", ["https://youtu.be/x"],
                      {"workers": 2, "no_vad": True})
        job = db.get_job("job-b")
        self.assertEqual(job["params"]["workers"], 2)
        self.assertTrue(job["params"]["no_vad"])
        self.assertEqual(job["urls"], ["https://youtu.be/x"])


class CommandBuilderTest(unittest.TestCase):
    def test_flags_and_values(self):
        job_dir = Path(tempfile.mkdtemp())
        cmd = pipeline.build_command(job_dir, {
            "model": "small", "workers": 2, "gpu_indices": [0, 1],
            "keep_video": True, "no_vad": True})
        text = " ".join(cmd)
        self.assertIn("--model small", text)
        self.assertIn("--workers 2", text)
        self.assertIn("--gpu-indices 0 1", text)
        self.assertIn("--keep-video", text)
        self.assertIn("--no-vad", text)
        self.assertIn("--url-file", text)
        self.assertIn(str(config.DOWNLOADER_SCRIPT), text)

    def test_defaults_omitted(self):
        cmd = pipeline.build_command(Path(tempfile.mkdtemp()), {})
        text = " ".join(cmd)
        for flag in ("--keep-video", "--no-vad", "--model", "--workers"):
            self.assertNotIn(flag, text)

    def test_zip_excludes_hidden_dirs(self):
        job_dir = Path(tempfile.mkdtemp())
        (job_dir / "outputs").mkdir()
        (job_dir / "work").mkdir()
        (job_dir / "outputs" / "a.mp3").write_bytes(b"mp3")
        (job_dir / "outputs" / "a.txt").write_text("t")
        (job_dir / "outputs" / ".checkpoints").mkdir()
        (job_dir / "outputs" / ".checkpoints" / "c.pcm").write_bytes(b"x")
        zip_path = pipeline.package_outputs(job_dir)
        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        self.assertIn("a.mp3", names)
        self.assertIn("a.txt", names)
        self.assertNotIn(".checkpoints/c.pcm", names)


if __name__ == "__main__":
    unittest.main()
