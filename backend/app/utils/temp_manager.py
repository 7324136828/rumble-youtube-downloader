"""System temp directory lifecycle for isolated job execution."""
import shutil
import tempfile
from pathlib import Path

from .. import config


def jobs_root() -> Path:
    root = config.JOBS_ROOT or (Path(tempfile.gettempdir()) / "prod_jobs")
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_job_dir(job_id: str) -> Path:
    job_dir = jobs_root() / job_id
    for sub in ("inputs", "work", "outputs", "archive"):
        (job_dir / sub).mkdir(parents=True, exist_ok=True)
    return job_dir


def purge_job_dir(path) -> None:
    if path:
        shutil.rmtree(Path(path), ignore_errors=True)
