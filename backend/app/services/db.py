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
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
)
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
