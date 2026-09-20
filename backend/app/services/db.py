"""SQLite-backed job registry."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .. import config

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    filename TEXT,
    file_size INTEGER,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT,
    temp_dir TEXT,
    zip_path TEXT,
    log_path TEXT,
    urls TEXT,
    params TEXT
);
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    connector TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT,
    quality TEXT,
    title TEXT,
    uploader TEXT,
    description TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    media_dir TEXT,
    file_path TEXT,
    file_size INTEGER,
    thumbnail_path TEXT,
    playback_warning TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS watch_history (
    video_id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    title TEXT,
    uploader TEXT,
    connector TEXT NOT NULL,
    duration REAL,
    position_seconds REAL NOT NULL DEFAULT 0,
    watched_seconds REAL NOT NULL DEFAULT 0,
    last_watched_at TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS watch_history_recent ON watch_history(last_watched_at DESC);
CREATE TABLE IF NOT EXISTS recommendation_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    model_id TEXT,
    seed_keywords TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO recommendation_settings (id) VALUES (1);
CREATE TABLE IF NOT EXISTS download_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    convert_for_browser INTEGER NOT NULL DEFAULT 0,
    conversion_opt_in INTEGER NOT NULL DEFAULT 0,
    generate_thumbnails INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO download_settings (id, convert_for_browser) VALUES (1, 0);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    conn = sqlite3.connect(config.JOBS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    config.JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, _connect() as conn:
        conn.executescript(_SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
        if "playback_warning" not in columns:
            conn.execute("ALTER TABLE videos ADD COLUMN playback_warning TEXT")
        settings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(download_settings)")}
        if "conversion_opt_in" not in settings_columns:
            # The previous default-on value did not record consent to convert.
            # Require a new explicit selection once, preserving later choices.
            conn.execute("ALTER TABLE download_settings ADD COLUMN conversion_opt_in INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE download_settings SET convert_for_browser=0")


def _download_settings_payload(row) -> dict:
    return {"convert_for_browser": bool(row["convert_for_browser"] and row["conversion_opt_in"]),
            "generate_thumbnails": bool(row["generate_thumbnails"])}


def get_download_settings() -> dict:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM download_settings WHERE id=1").fetchone()
    return _download_settings_payload(row)


def update_download_settings(changes: dict) -> dict:
    allowed = {"convert_for_browser", "generate_thumbnails"}
    if changes.keys() - allowed or any(type(value) is not bool for value in changes.values()):
        raise ValueError("Download settings must contain only the supported boolean fields.")
    values = dict(changes)
    if "convert_for_browser" in values:
        values["conversion_opt_in"] = values["convert_for_browser"]
    with _LOCK, _connect() as conn:
        if values:
            columns = ", ".join(f"{key}=?" for key in values)
            conn.execute(f"UPDATE download_settings SET {columns} WHERE id=1", tuple(values.values()))
        row = conn.execute("SELECT * FROM download_settings WHERE id=1").fetchone()
    return _download_settings_payload(row)


def create_job(job_id, filename, file_size, temp_dir, urls, params) -> dict:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, filename, file_size, status, progress, created_at,"
            " temp_dir, urls, params) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, filename, file_size, "queued", 0, _now(), str(temp_dir),
             json.dumps(urls), json.dumps(params)),
        )
    return get_job(job_id)


def update_job(job_id, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
    with _LOCK, _connect() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*values, job_id))


def get_job(job_id) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _decode(dict(row)) if row else None


def list_jobs() -> list:
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [_decode(dict(row)) for row in rows]


def fail_stale_jobs() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', error_message = ?, completed_at = ?"
            " WHERE status IN ('queued', 'in_progress')",
            ("Server restarted while the job was running.", _now()),
        )


def create_video(video_id, source_url, connector, quality, media_dir) -> dict:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO videos (id, source_url, connector, status, progress,"
            " quality, media_dir, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (video_id, source_url, connector, "queued", 0, quality,
             str(media_dir), _now()),
        )
    return get_video(video_id)


def update_video(video_id, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
    with _LOCK, _connect() as conn:
        conn.execute(f"UPDATE videos SET {columns} WHERE id = ?",
                     (*values, video_id))


def get_video(video_id) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?",
                           (video_id,)).fetchone()
    return dict(row) if row else None


def list_videos(status: str | None = None) -> list:
    with _LOCK, _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM videos WHERE status = ?"
                " ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM videos ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def delete_video(video_id) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))


def record_watch(video_id: str, position_seconds: float, watched_seconds: float,
                 completed: bool = False) -> dict | None:
    """Keep metadata snapshots so history survives removal of downloaded files."""
    with _LOCK, _connect() as conn:
        video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if video is None:
            raise LookupError("Video not found")
        if video["status"] != "ready":
            raise ValueError("Only ready videos can be recorded in watch history.")
        previous = conn.execute("SELECT * FROM watch_history WHERE video_id = ?",
                                (video_id,)).fetchone()
        if watched_seconds <= 0 and previous is None:
            return None
        if video["duration"] and video["duration"] > 0:
            position_seconds = min(position_seconds, video["duration"])
        conn.execute(
            "INSERT INTO watch_history (video_id, source_url, title, uploader, connector,"
            " duration, position_seconds, watched_seconds, last_watched_at, completed)"
            " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET"
            " title=excluded.title, uploader=excluded.uploader, duration=excluded.duration,"
            " position_seconds=excluded.position_seconds,"
            " watched_seconds=watch_history.watched_seconds+excluded.watched_seconds,"
            " last_watched_at=excluded.last_watched_at,"
            " completed=MAX(watch_history.completed, excluded.completed)",
            (video_id, video["source_url"], video["title"], video["uploader"], video["connector"],
             video["duration"], position_seconds, watched_seconds, _now(), int(completed)))
        row = dict(conn.execute("SELECT * FROM watch_history WHERE video_id = ?",
                                (video_id,)).fetchone())
    row["completed"] = bool(row["completed"])
    return row


def list_watch_history(limit: int = 30) -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM watch_history WHERE watched_seconds > 0"
            " ORDER BY last_watched_at DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(row), "completed": bool(row["completed"])} for row in rows]


def _settings_payload(row) -> dict:
    return {"enabled": bool(row["enabled"]), "model_id": row["model_id"],
            "seed_keywords": json.loads(row["seed_keywords"]), "revision": row["revision"]}


def get_recommendation_settings() -> dict:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone()
    return _settings_payload(row)


class SettingsConflictError(ValueError):
    """A preference request was superseded while validating its configuration."""


def update_recommendation_settings(changes: dict, expected_revision: int | None = None) -> dict:
    allowed = {"enabled", "model_id", "seed_keywords"}
    if changes.keys() - allowed:
        raise ValueError("Unknown recommendation setting")
    values = dict(changes)
    if "seed_keywords" in values:
        values["seed_keywords"] = json.dumps(values["seed_keywords"])
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone()
        if expected_revision is not None and row["revision"] != expected_revision:
            raise SettingsConflictError("Recommendation settings changed. Please try again.")
        if values:
            columns = ", ".join(f"{key}=?" for key in values)
            conn.execute(f"UPDATE recommendation_settings SET {columns}, revision=revision+1 WHERE id=1",
                         tuple(values.values()))
        row = conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone()
    return _settings_payload(row)


def fail_stale_videos() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE videos SET status = 'failed', error_message = ?,"
            " completed_at = ?"
            " WHERE status IN ('queued', 'downloading', 'processing')",
            ("Server restarted while the download was running.", _now()),
        )


def _decode(job: dict) -> dict:
    for key in ("urls", "params"):
        if job.get(key):
            try:
                job[key] = json.loads(job[key])
            except (ValueError, TypeError):
                pass
    return job
