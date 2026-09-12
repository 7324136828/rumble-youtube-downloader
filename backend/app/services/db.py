"""SQLite-backed job registry."""
import json
import sqlite3
import threading
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
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.JOBS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    config.JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, _connect() as conn:
        conn.execute(_SCHEMA)


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


def _decode(job: dict) -> dict:
    for key in ("urls", "params"):
        if job.get(key):
            try:
                job[key] = json.loads(job[key])
            except (ValueError, TypeError):
                pass
    return job
