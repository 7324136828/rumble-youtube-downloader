"""Environment settings for the productionized backend."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_PROJECT_DIR = Path(
    os.environ.get("ORIGINAL_PROJECT_DIR", REPO_ROOT / "skill" / "original-project")
).resolve()
DOWNLOADER_SCRIPT = ORIGINAL_PROJECT_DIR / "download_rumble.py"

JOBS_DB_PATH = Path(
    os.environ.get("JOBS_DB_PATH", REPO_ROOT / "backend" / "jobs.db")
).resolve()

JOBS_ROOT = Path(
    os.environ.get("JOBS_ROOT", "") or ""
) if os.environ.get("JOBS_ROOT") else None

BACKEND_HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
