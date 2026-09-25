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
MAX_UPLOAD_BYTES = max(1, int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 ** 3))))
MAX_THUMBNAIL_BYTES = 10 * 1024 ** 2

# Optional Netscape-format cookie file. Browser-cookie extraction is an explicit
# persisted download preference; this environment override supports headless hosts.
_YTDLP_COOKIE_FILE = os.environ.get("YTDLP_COOKIE_FILE", "").strip()
YTDLP_COOKIE_FILE = Path(_YTDLP_COOKIE_FILE).expanduser().resolve() if _YTDLP_COOKIE_FILE else None

# The Connector owns provider credentials and saved model-routing configurations.
RECOMMENDATION_CONNECTOR_URL = (
    os.environ.get("RECOMMENDATION_CONNECTOR_URL") or "http://127.0.0.1:8301"
).rstrip("/")

BACKEND_HOST = os.environ.get("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8000"))

# Address reachable from both The Connector backend and its browser player.
CONNECTOR_PUBLIC_URL = (os.environ.get("CONNECTOR_PUBLIC_URL") or
                        f"http://127.0.0.1:{BACKEND_PORT}").rstrip("/")
