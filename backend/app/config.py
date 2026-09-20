"""Environment settings for the productionized backend."""
import os
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_PROJECT_DIR = Path(
    os.environ.get("ORIGINAL_PROJECT_DIR", REPO_ROOT / "skill" / "original-project")
).resolve()
DOWNLOADER_SCRIPT = ORIGINAL_PROJECT_DIR / "download_rumble.py"

JOBS_ROOT = Path(
    os.environ.get("JOBS_ROOT") or Path(tempfile.gettempdir()) / "prod_jobs"
).resolve()

JOBS_DB_PATH = Path(
    os.environ.get("JOBS_DB_PATH") or JOBS_ROOT / "jobs.db"
).resolve()

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT") or JOBS_ROOT / "library").resolve()
MAX_CONCURRENT_DOWNLOADS = max(1, int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "2")))

# The Connector owns provider credentials and saved model-routing configurations.
RECOMMENDATION_CONNECTOR_URL = (
    os.environ.get("RECOMMENDATION_CONNECTOR_URL") or "http://127.0.0.1:8301"
).rstrip("/")

BACKEND_HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))
