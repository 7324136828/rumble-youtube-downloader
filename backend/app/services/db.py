"""SQLite-backed job registry."""
import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .. import config
from ..schemas.recommendation_providers import default_providers
from ..schemas.recommendations import (RecommendationSettingsPatch, WatchLaterImport,
                                       default_fallback_weights)
from ..schemas.settings import normalize_cookie_file_path

_LOCK = threading.Lock()
# File deletion and policy edits must agree on whether a video is still expired.
# Always acquire this before _LOCK when both are needed.
RETENTION_POLICY_LOCK = threading.RLock()

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
    completed_at TEXT,
    retention_days INTEGER,
    retention_override INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS video_keyword_jobs (
    video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'idle' CHECK (status IN ('idle','pending','ready','failed')),
    model_id TEXT,
    error_message TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_keywords (
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL COLLATE NOCASE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (video_id, keyword)
);
CREATE INDEX IF NOT EXISTS video_keywords_keyword ON video_keywords(keyword COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS media_conversions (
    video_id TEXT NOT NULL,
    format TEXT NOT NULL,
    status TEXT NOT NULL,
    output_path TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (video_id, format)
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
    custom_prompt TEXT NOT NULL DEFAULT '',
    allow_unverified_links INTEGER NOT NULL DEFAULT 0,
    allow_ai_title_lookup INTEGER NOT NULL DEFAULT 0,
    fetch_all_search_links INTEGER NOT NULL DEFAULT 0,
    providers TEXT,
    fallback_weights TEXT,
    revision INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO recommendation_settings (id) VALUES (1);
CREATE TABLE IF NOT EXISTS recommendation_websites (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    search_url TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendation_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    website_id TEXT NOT NULL REFERENCES recommendation_websites(id),
    source_url TEXT NOT NULL UNIQUE,
    provider_video_id TEXT NOT NULL,
    title TEXT,
    description TEXT,
    uploader TEXT,
    thumbnail_url TEXT,
    duration REAL,
    view_count INTEGER,
    uploaded_at TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    verification TEXT,
    user_title TEXT,
    user_description TEXT,
    title_fetch_status TEXT NOT NULL DEFAULT 'idle',
    title_fetch_error TEXT,
    title_fetch_token TEXT,
    title_fetch_method TEXT,
    thumbnail_path TEXT,
    thumbnail_fetch_status TEXT NOT NULL DEFAULT 'idle',
    thumbnail_fetch_error TEXT,
    thumbnail_fetch_token TEXT,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    recommended_count INTEGER NOT NULL DEFAULT 0,
    fallback_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_discovered_at TEXT,
    last_recommended_at TEXT
);
CREATE TABLE IF NOT EXISTS recommendation_video_origins (
    video_id INTEGER NOT NULL REFERENCES recommendation_videos(id),
    origin TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (video_id, origin)
);
CREATE TABLE IF NOT EXISTS recommendation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES recommendation_videos(id),
    recommended_at TEXT NOT NULL,
    fallback INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS recommendation_videos_website ON recommendation_videos(website_id);
CREATE INDEX IF NOT EXISTS recommendation_origins_kind ON recommendation_video_origins(origin, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS recommendation_history_video ON recommendation_history(video_id, recommended_at DESC);
CREATE TABLE IF NOT EXISTS watch_later_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    source_page TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS watch_later_links_recent ON watch_later_links(updated_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS download_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    convert_for_browser INTEGER NOT NULL DEFAULT 0,
    conversion_opt_in INTEGER NOT NULL DEFAULT 0,
    generate_thumbnails INTEGER NOT NULL DEFAULT 1,
    auto_delete_enabled INTEGER NOT NULL DEFAULT 1,
    retention_days INTEGER NOT NULL DEFAULT 7,
    cookie_browser TEXT NOT NULL DEFAULT '',
    cookie_browser_profile TEXT NOT NULL DEFAULT '',
    cookie_file TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO download_settings (id, convert_for_browser) VALUES (1, 0);
CREATE TABLE IF NOT EXISTS link_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    hide_repeated_links INTEGER NOT NULL DEFAULT 1,
    manual_review_version INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO link_settings (id) VALUES (1);
CREATE TABLE IF NOT EXISTS link_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    title TEXT,
    provider TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','silenced','allowed','excluded')),
    seen_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_session TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS link_history_state_recent ON link_history(state, last_seen_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    conn = sqlite3.connect(config.JOBS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
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
        if "retention_days" not in columns:
            conn.execute("ALTER TABLE videos ADD COLUMN retention_days INTEGER")
        if "retention_override" not in columns:
            conn.execute("ALTER TABLE videos ADD COLUMN retention_override INTEGER NOT NULL DEFAULT 0")
        if "expires_at" not in columns:
            conn.execute("ALTER TABLE videos ADD COLUMN expires_at TEXT")
        settings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(download_settings)")}
        if "conversion_opt_in" not in settings_columns:
            # The previous default-on value did not record consent to convert.
            # Require a new explicit selection once, preserving later choices.
            conn.execute("ALTER TABLE download_settings ADD COLUMN conversion_opt_in INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE download_settings SET convert_for_browser=0")
        if "auto_delete_enabled" not in settings_columns:
            conn.execute("ALTER TABLE download_settings ADD COLUMN auto_delete_enabled INTEGER NOT NULL DEFAULT 1")
        if "retention_days" not in settings_columns:
            conn.execute("ALTER TABLE download_settings ADD COLUMN retention_days INTEGER NOT NULL DEFAULT 7")
        if "cookie_browser" not in settings_columns:
            conn.execute("ALTER TABLE download_settings ADD COLUMN cookie_browser TEXT NOT NULL DEFAULT ''")
        if "cookie_browser_profile" not in settings_columns:
            conn.execute("ALTER TABLE download_settings ADD COLUMN cookie_browser_profile TEXT NOT NULL DEFAULT ''")
        if "cookie_file" not in settings_columns:
            conn.execute("ALTER TABLE download_settings ADD COLUMN cookie_file TEXT NOT NULL DEFAULT ''")
        link_settings_columns = {row["name"] for row in conn.execute("PRAGMA table_info(link_settings)")}
        if "manual_review_version" not in link_settings_columns:
            # Earlier builds automatically classified links seen in a later
            # session as silenced. Return those decisions to the user exactly
            # once; from this version onward only an explicit action can mark a
            # link as repeated.
            conn.execute("ALTER TABLE link_settings ADD COLUMN manual_review_version INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE link_history SET state='active' WHERE state='silenced'")
            conn.execute("UPDATE link_settings SET manual_review_version=1 WHERE id=1")
        recommendation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(recommendation_settings)")}
        if "allow_unverified_links" not in recommendation_columns:
            conn.execute("ALTER TABLE recommendation_settings ADD COLUMN allow_unverified_links INTEGER NOT NULL DEFAULT 0")
        if "custom_prompt" not in recommendation_columns:
            conn.execute("ALTER TABLE recommendation_settings ADD COLUMN custom_prompt TEXT NOT NULL DEFAULT ''")
        if "allow_ai_title_lookup" not in recommendation_columns:
            conn.execute("ALTER TABLE recommendation_settings ADD COLUMN allow_ai_title_lookup INTEGER NOT NULL DEFAULT 0")
        if "fetch_all_search_links" not in recommendation_columns:
            conn.execute("ALTER TABLE recommendation_settings ADD COLUMN fetch_all_search_links INTEGER NOT NULL DEFAULT 0")
        if "providers" not in recommendation_columns:
            conn.execute("ALTER TABLE recommendation_settings ADD COLUMN providers TEXT")
        if "fallback_weights" not in recommendation_columns:
            conn.execute("ALTER TABLE recommendation_settings ADD COLUMN fallback_weights TEXT")
        catalog_columns = {row["name"] for row in conn.execute("PRAGMA table_info(recommendation_videos)")}
        if "title_fetch_status" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN title_fetch_status TEXT NOT NULL DEFAULT 'idle'")
        if "title_fetch_error" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN title_fetch_error TEXT")
        if "title_fetch_token" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN title_fetch_token TEXT")
        if "title_fetch_method" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN title_fetch_method TEXT")
        if "thumbnail_path" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN thumbnail_path TEXT")
        if "thumbnail_fetch_status" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN thumbnail_fetch_status TEXT NOT NULL DEFAULT 'idle'")
        if "thumbnail_fetch_error" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN thumbnail_fetch_error TEXT")
        if "thumbnail_fetch_token" not in catalog_columns:
            conn.execute("ALTER TABLE recommendation_videos ADD COLUMN thumbnail_fetch_token TEXT")
        # In-memory workers cannot survive a process restart. Their tokens must
        # also be retired so a late result cannot complete a restarted lookup.
        conn.execute("UPDATE recommendation_videos SET title_fetch_status='idle', title_fetch_error=NULL,"
                     " title_fetch_token=NULL WHERE title_fetch_status='pending'")
        conn.execute("UPDATE recommendation_videos SET thumbnail_fetch_status='idle', thumbnail_fetch_error=NULL,"
                     " thumbnail_fetch_token=NULL WHERE thumbnail_fetch_status='pending'")
        conn.execute("UPDATE video_keyword_jobs SET status='idle', error_message=NULL"
                     " WHERE status='pending'")
        row = conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone()
        _sync_recommendation_websites(conn, _settings_payload(row)["providers"])

    from . import connector_activity
    connector_activity.init_db()


def _download_settings_payload(row) -> dict:
    retention_days = row["retention_days"] if row["auto_delete_enabled"] else -1
    return {"convert_for_browser": bool(row["convert_for_browser"] and row["conversion_opt_in"]),
            "generate_thumbnails": bool(row["generate_thumbnails"]),
            "auto_delete_enabled": retention_days > 0,
            "retention_days": retention_days,
            "cookie_browser": row["cookie_browser"],
            "cookie_browser_profile": row["cookie_browser_profile"],
            "cookie_file": row["cookie_file"]}


def get_download_settings() -> dict:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM download_settings WHERE id=1").fetchone()
    return _download_settings_payload(row)


def update_download_settings(changes: dict) -> dict:
    allowed = {"convert_for_browser", "generate_thumbnails", "auto_delete_enabled", "retention_days",
               "cookie_browser", "cookie_browser_profile", "cookie_file"}
    boolean_fields = {"convert_for_browser", "generate_thumbnails", "auto_delete_enabled"}
    if changes.keys() - allowed or any(type(changes[key]) is not bool for key in changes.keys() & boolean_fields):
        raise ValueError("Download settings contain unsupported values.")
    if "retention_days" in changes and (type(changes["retention_days"]) is not int
                                        or changes["retention_days"] == 0
                                        or changes["retention_days"] > 3650):
        raise ValueError("Retention days must be negative for indefinite storage or between 1 and 3650.")
    browsers = {"", "brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"}
    if ("cookie_browser" in changes and (
            not isinstance(changes["cookie_browser"], str) or changes["cookie_browser"] not in browsers)) or (
            "cookie_browser_profile" in changes and (
                not isinstance(changes["cookie_browser_profile"], str)
                or len(changes["cookie_browser_profile"]) > 500
                or any(ord(character) < 32 for character in changes["cookie_browser_profile"]))):
        raise ValueError("Download cookie settings contain unsupported values.")
    values = dict(changes)
    if "cookie_file" in values:
        values["cookie_file"] = normalize_cookie_file_path(values["cookie_file"])
    if "cookie_browser_profile" in values:
        values["cookie_browser_profile"] = values["cookie_browser_profile"].strip()
    if "convert_for_browser" in values:
        values["conversion_opt_in"] = values["convert_for_browser"]
    with RETENTION_POLICY_LOCK, _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT * FROM download_settings WHERE id=1").fetchone()
        selected_browser = values.get("cookie_browser", current["cookie_browser"])
        if "cookie_browser" in values and not selected_browser:
            values["cookie_browser_profile"] = ""
        selected_profile = values.get("cookie_browser_profile", current["cookie_browser_profile"])
        if not selected_browser and selected_profile:
            raise ValueError("Choose a browser before entering a browser profile.")
        retention_changed = bool(changes.keys() & {"retention_days", "auto_delete_enabled"})
        if "retention_days" in values:
            values["retention_days"] = (-1 if values["retention_days"] < 0
                                        or values.get("auto_delete_enabled") is False
                                        else values["retention_days"])
            values["auto_delete_enabled"] = values["retention_days"] > 0
        elif "auto_delete_enabled" in values:
            values["retention_days"] = ((current["retention_days"] if current["retention_days"] > 0 else 7)
                                        if values["auto_delete_enabled"] else -1)
        if values:
            columns = ", ".join(f"{key}=?" for key in values)
            conn.execute(f"UPDATE download_settings SET {columns} WHERE id=1", tuple(values.values()))
        if retention_changed:
            retention_days = values["retention_days"]
            rows = conn.execute(
                "SELECT id,status,created_at,completed_at FROM videos"
                " WHERE retention_override=0"
                " AND status IN ('queued','downloading','processing','ready')").fetchall()
            for video in rows:
                expires_at = _video_expiration(video, retention_days)
                conn.execute("UPDATE videos SET retention_days=?, expires_at=? WHERE id=?",
                             (retention_days if retention_days > 0 else None, expires_at, video["id"]))
        row = conn.execute("SELECT * FROM download_settings WHERE id=1").fetchone()
    return _download_settings_payload(row)


def get_link_settings() -> dict:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT hide_repeated_links FROM link_settings WHERE id=1").fetchone()
    return {"hide_repeated_links": bool(row["hide_repeated_links"])}


def update_link_settings(changes: dict) -> dict:
    if changes.keys() - {"hide_repeated_links"} or any(type(value) is not bool for value in changes.values()):
        raise ValueError("Link settings contain unsupported values.")
    with _LOCK, _connect() as conn:
        if changes:
            conn.execute("UPDATE link_settings SET hide_repeated_links=? WHERE id=1",
                         (int(changes["hide_repeated_links"]),))
        row = conn.execute("SELECT hide_repeated_links FROM link_settings WHERE id=1").fetchone()
    return {"hide_repeated_links": bool(row["hide_repeated_links"])}


def filter_presented_links(items: list[dict], session_id: str) -> list[dict]:
    """Remove only links the user explicitly excluded or marked repeated."""
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 64:
        raise ValueError("A valid link session is required.")
    visible = []
    with _LOCK, _connect() as conn:
        hide_repeated = bool(conn.execute(
            "SELECT hide_repeated_links FROM link_settings WHERE id=1").fetchone()[0])
        seen = set()
        for item in items:
            canonical_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(canonical_id, str) or not canonical_id or canonical_id in seen:
                continue
            seen.add(canonical_id)
            row = conn.execute("SELECT * FROM link_history WHERE canonical_id=?",
                               (canonical_id,)).fetchone()
            if row and row["state"] == "excluded":
                continue
            if row and hide_repeated and row["state"] == "silenced":
                continue
            visible.append(item)
    return visible


def record_presented_links(items: list[dict], session_id: str) -> list[dict]:
    """Persist every presented canonical link and return its management id."""
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 64:
        raise ValueError("A valid link session is required.")
    now = _now()
    result = []
    with _LOCK, _connect() as conn:
        for item in items:
            canonical_id = item.get("id") if isinstance(item, dict) else None
            source_url = item.get("source_url") if isinstance(item, dict) else None
            provider = item.get("connector") if isinstance(item, dict) else None
            if not all(isinstance(value, str) and value for value in (canonical_id, source_url, provider)):
                continue
            title = item.get("title") if isinstance(item.get("title"), str) else None
            row = conn.execute("SELECT id,last_session FROM link_history WHERE canonical_id=?",
                               (canonical_id,)).fetchone()
            if row is None:
                cursor = conn.execute(
                    "INSERT INTO link_history (canonical_id,source_url,title,provider,state,seen_count,"
                    " first_seen_at,last_seen_at,last_session) VALUES (?,?,?,?, 'active',1,?,?,?)",
                    (canonical_id, source_url, title, provider, now, now, session_id))
                link_id = cursor.lastrowid
            else:
                increment = int(row["last_session"] != session_id)
                conn.execute(
                    "UPDATE link_history SET source_url=?, title=COALESCE(?,title), provider=?,"
                    " seen_count=seen_count+?, last_seen_at=?, last_session=? WHERE id=?",
                    (source_url, title, provider, increment, now, session_id, row["id"]))
                link_id = row["id"]
            result.append({**item, "link_id": link_id})
    return result


def list_link_history(state: str = "all", limit: int = 200, offset: int = 0) -> dict:
    if state not in ("all", "active", "silenced", "allowed", "excluded"):
        raise ValueError("Unknown link state.")
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise ValueError("Invalid link history page.")
    where, params = ("", []) if state == "all" else (" WHERE state=?", [state])
    with _LOCK, _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM link_history" + where, params).fetchone()[0]
        rows = conn.execute(
            "SELECT id,canonical_id,source_url,title,provider,state,seen_count,first_seen_at,last_seen_at"
            + " FROM link_history" + where + " ORDER BY last_seen_at DESC,id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def update_link_state(link_id: int, state: str) -> dict | None:
    if state not in ("active", "allowed", "silenced", "excluded"):
        raise ValueError("Link state must be unreviewed, allowed, repeated, or excluded.")
    with _LOCK, _connect() as conn:
        cursor = conn.execute("UPDATE link_history SET state=? WHERE id=?", (state, link_id))
        if not cursor.rowcount:
            return None
        row = conn.execute(
            "SELECT id,canonical_id,source_url,title,provider,state,seen_count,first_seen_at,last_seen_at"
            " FROM link_history WHERE id=?", (link_id,)).fetchone()
    return dict(row)


def delete_link_history(link_id: int) -> bool:
    """Delete one local presentation record without changing any video data."""
    with _LOCK, _connect() as conn:
        return bool(conn.execute("DELETE FROM link_history WHERE id=?", (link_id,)).rowcount)


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


def create_video(video_id, source_url, connector, quality, media_dir,
                 retention_days: int | None = None) -> dict:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO videos (id, source_url, connector, status, progress,"
            " quality, media_dir, created_at, retention_days) VALUES (?,?,?,?,?,?,?,?,?)",
            (video_id, source_url, connector, "queued", 0, quality,
             str(media_dir), _now(), retention_days),
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


def _video_expiration(video, retention_days: int | None) -> str | None:
    if not retention_days or retention_days < 0 or video["status"] != "ready":
        return None
    try:
        downloaded_at = video["completed_at"] or video["created_at"]
        completed = datetime.fromisoformat(downloaded_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return (completed + timedelta(days=retention_days)).isoformat()


def update_video_retention(video_id: str, retention_days: int) -> dict | None:
    if type(retention_days) is not int or retention_days == 0 or retention_days > 3650:
        raise ValueError("Retention days must be negative for indefinite storage or between 1 and 3650.")
    days = retention_days if retention_days > 0 else None
    with RETENTION_POLICY_LOCK, _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        video = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if video is None:
            return None
        conn.execute("UPDATE videos SET retention_days=?, expires_at=?, retention_override=1 WHERE id=?",
                     (days, _video_expiration(video, days), video_id))
        row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
    return dict(row)


def complete_video(video_id: str, completed_at: str, **fields) -> None:
    """Publish completion and the current retention deadline in one transaction."""
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if row is None:
            return
        video = dict(row)
        video.update(status="ready", completed_at=completed_at)
        fields.update(status="ready", completed_at=completed_at,
                      expires_at=_video_expiration(video, video["retention_days"]))
        columns = ", ".join(f"{key}=?" for key in fields)
        conn.execute(f"UPDATE videos SET {columns} WHERE id=?", (*fields.values(), video_id))


def get_video(video_id) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?",
                           (video_id,)).fetchone()
    return dict(row) if row else None


def get_video_keywords(video_id: str) -> list[str]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT keyword FROM video_keywords WHERE video_id=? ORDER BY position, keyword",
            (video_id,),
        ).fetchall()
    return [row["keyword"] for row in rows]


def begin_video_keyword_generation(video_id: str, model_id: str) -> dict | None:
    """Claim a ready video that does not already have generated keywords."""
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        video = conn.execute(
            "SELECT * FROM videos WHERE id=? AND status='ready'", (video_id,)
        ).fetchone()
        if video is None or not ((video["title"] or "").strip()
                                 or (video["description"] or "").strip()):
            return None
        job = conn.execute(
            "SELECT status FROM video_keyword_jobs WHERE video_id=?", (video_id,)
        ).fetchone()
        if job is not None and job["status"] in ("pending", "ready"):
            return None
        conn.execute(
            "INSERT INTO video_keyword_jobs (video_id,status,model_id,error_message,updated_at)"
            " VALUES (?, 'pending', ?, NULL, ?)"
            " ON CONFLICT(video_id) DO UPDATE SET status='pending', model_id=excluded.model_id,"
            " error_message=NULL, updated_at=excluded.updated_at",
            (video_id, model_id, _now()),
        )
    return dict(video)


def finish_video_keyword_generation(video_id: str, keywords: list[str]) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute(
            "SELECT status FROM video_keyword_jobs WHERE video_id=?", (video_id,)
        ).fetchone()
        if job is None or job["status"] != "pending":
            return
        conn.execute("DELETE FROM video_keywords WHERE video_id=?", (video_id,))
        conn.executemany(
            "INSERT INTO video_keywords (video_id,keyword,position) VALUES (?,?,?)",
            [(video_id, keyword, position) for position, keyword in enumerate(keywords)],
        )
        conn.execute(
            "UPDATE video_keyword_jobs SET status='ready', error_message=NULL, updated_at=?"
            " WHERE video_id=?", (_now(), video_id)
        )


def fail_video_keyword_generation(video_id: str, message: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE video_keyword_jobs SET status='failed', error_message=?, updated_at=?"
            " WHERE video_id=? AND status='pending'", (message[:500], _now(), video_id)
        )


def reset_video_keyword_generation(video_id: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE video_keyword_jobs SET status='idle', error_message=NULL, updated_at=?"
            " WHERE video_id=? AND status='pending'", (_now(), video_id)
        )


def list_videos_for_keyword_generation() -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT videos.* FROM videos LEFT JOIN video_keyword_jobs"
            " ON video_keyword_jobs.video_id=videos.id"
            " WHERE videos.status='ready'"
            " AND (COALESCE(videos.title,'') <> '' OR COALESCE(videos.description,'') <> '')"
            " AND COALESCE(video_keyword_jobs.status,'idle') IN ('idle','failed')"
            " ORDER BY videos.created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def video_keyword_catalog(query: str = "") -> dict:
    """Return keyword counts plus keyword-tagged ready videos for local search."""
    needle = " ".join(query.split()).casefold()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT videos.*, video_keywords.keyword, video_keywords.position"
            " FROM videos JOIN video_keywords ON video_keywords.video_id=videos.id"
            " WHERE videos.status='ready'"
            " ORDER BY videos.created_at DESC, video_keywords.position, video_keywords.keyword"
        ).fetchall()
        statuses = conn.execute(
            "SELECT status, COUNT(*) AS count FROM video_keyword_jobs GROUP BY status"
        ).fetchall()
    videos, counts = {}, {}
    for row in rows:
        item = dict(row)
        keyword = item.pop("keyword")
        item.pop("position")
        counts[keyword] = counts.get(keyword, 0) + 1
        video = videos.setdefault(item["id"], {**item, "keywords": []})
        video["keywords"].append(keyword)
    matches = list(videos.values())
    if needle:
        matches = [video for video in matches if any(
            needle in keyword.casefold() for keyword in video["keywords"])]
    cloud = [{"keyword": keyword, "count": count} for keyword, count in counts.items()]
    cloud.sort(key=lambda item: (-item["count"], item["keyword"].casefold()))
    return {"query": query, "keywords": cloud, "videos": matches,
            "status": {row["status"]: row["count"] for row in statuses}}


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


def list_expired_videos(now: str | None = None) -> list[dict]:
    try:
        current = datetime.fromisoformat(now.replace("Z", "+00:00")) if now else datetime.now(timezone.utc)
    except (AttributeError, ValueError) as exc:
        raise ValueError("Expiration time must be an ISO timestamp.") from exc
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM videos WHERE status='ready' AND retention_days > 0"
            " ORDER BY COALESCE(completed_at,created_at)").fetchall()
    expired = []
    for row in rows:
        value = dict(row)
        try:
            downloaded = datetime.fromisoformat((value.get("completed_at") or value["created_at"]).replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        if downloaded.tzinfo is None:
            downloaded = downloaded.replace(tzinfo=timezone.utc)
        if current >= downloaded + timedelta(days=value["retention_days"]):
            expired.append(value)
    return expired


def delete_video(video_id) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM media_conversions WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))


def upsert_media_conversion(video_id: str, output_format: str,
                            status: str) -> dict:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO media_conversions (video_id, format, status, created_at)"
            " VALUES (?,?,?,?) ON CONFLICT(video_id, format) DO UPDATE SET"
            " status=excluded.status, output_path=NULL, error_message=NULL,"
            " completed_at=NULL, created_at=excluded.created_at",
            (video_id, output_format, status, _now()),
        )
        row = conn.execute(
            "SELECT * FROM media_conversions WHERE video_id = ? AND format = ?",
            (video_id, output_format),
        ).fetchone()
    return dict(row)


def update_media_conversion(video_id: str, output_format: str, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    with _LOCK, _connect() as conn:
        conn.execute(
            f"UPDATE media_conversions SET {columns}"
            " WHERE video_id = ? AND format = ?",
            (*fields.values(), video_id, output_format),
        )


def get_media_conversion(video_id: str, output_format: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM media_conversions WHERE video_id = ? AND format = ?",
            (video_id, output_format),
        ).fetchone()
    return dict(row) if row else None


def list_media_conversions(video_id: str) -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM media_conversions WHERE video_id = ? ORDER BY format",
            (video_id,),
        ).fetchall()
    return [dict(row) for row in rows]


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
            "SELECT watch_history.*, (SELECT videos.id FROM videos"
            " WHERE videos.source_url = watch_history.source_url"
            " AND videos.status = 'ready' ORDER BY videos.created_at DESC LIMIT 1) AS media_id,"
            " (SELECT videos.id FROM videos"
            " WHERE videos.source_url = watch_history.source_url"
            " AND videos.status = 'ready' AND videos.thumbnail_path IS NOT NULL"
            " ORDER BY videos.created_at DESC LIMIT 1) AS thumbnail_media_id"
            " FROM watch_history WHERE watched_seconds > 0"
            " ORDER BY last_watched_at DESC LIMIT ?", (limit,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        thumbnail_media_id = item.pop("thumbnail_media_id")
        item["thumbnail_url"] = (f"/api/media/{thumbnail_media_id}/thumbnail"
                                 if thumbnail_media_id else None)
        item["completed"] = bool(item["completed"])
        result.append(item)
    return result


def _settings_payload(row) -> dict:
    stored_weights = (json.loads(row["fallback_weights"])
                      if row["fallback_weights"] is not None else default_fallback_weights())
    if isinstance(stored_weights, dict) and stored_weights.get("public_search", 0) != 0:
        website = stored_weights.get("custom_search", 0)
        saved = stored_weights.get("watch_later", 0)
        remaining = website + saved
        website = round(website * 100 / remaining) if remaining > 0 else 50
        stored_weights = {"custom_search": website, "public_search": 0,
                          "watch_later": 100 - website}
    return {"enabled": bool(row["enabled"]), "model_id": row["model_id"],
            "seed_keywords": json.loads(row["seed_keywords"]),
            "custom_prompt": row["custom_prompt"],
            "allow_unverified_links": bool(row["allow_unverified_links"]),
            "allow_ai_title_lookup": bool(row["allow_ai_title_lookup"]),
            "fetch_all_search_links": bool(row["fetch_all_search_links"]),
            "providers": json.loads(row["providers"]) if row["providers"] is not None else default_providers(),
            "fallback_weights": stored_weights,
            "revision": row["revision"]}


def get_recommendation_settings() -> dict:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone()
    return _settings_payload(row)


class SettingsConflictError(ValueError):
    """A preference request was superseded while validating its configuration."""


def update_recommendation_settings(changes: dict, expected_revision: int | None = None) -> dict:
    allowed = {"enabled", "model_id", "seed_keywords", "custom_prompt", "allow_unverified_links", "allow_ai_title_lookup", "fetch_all_search_links", "providers", "fallback_weights"}
    if changes.keys() - allowed:
        raise ValueError("Unknown recommendation setting")
    values = dict(changes)
    if "seed_keywords" in values:
        values["seed_keywords"] = json.dumps(values["seed_keywords"])
    if "providers" in values:
        validated = RecommendationSettingsPatch(providers=values["providers"])
        values["providers"] = json.dumps(validated.model_dump()["providers"])
    if "fallback_weights" in values:
        validated = RecommendationSettingsPatch(fallback_weights=values["fallback_weights"])
        values["fallback_weights"] = json.dumps(validated.model_dump()["fallback_weights"])
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone()
        if expected_revision is not None and row["revision"] != expected_revision:
            raise SettingsConflictError("Recommendation settings changed. Please try again.")
        if values:
            columns = ", ".join(f"{key}=?" for key in values)
            changed = conn.execute(
                f"UPDATE recommendation_settings SET {columns}, revision=revision+1 WHERE id=1 AND revision=?",
                (*values.values(), row["revision"])).rowcount
            if changed != 1:
                raise SettingsConflictError("Recommendation settings changed. Please try again.")
        row = conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone()
        if "providers" in values:
            _sync_recommendation_websites(conn, json.loads(values["providers"]))
    return _settings_payload(row)


def _sync_recommendation_websites(conn, providers) -> None:
    """Mirror settings while retaining removed websites referenced by the catalog."""
    now = _now()
    conn.execute("UPDATE recommendation_websites SET active=0, enabled=0")
    for position, provider in enumerate(providers):
        conn.execute(
            "INSERT INTO recommendation_websites"
            " (id, domain, name, enabled, active, search_url, position, created_at, updated_at)"
            " VALUES (?,?,?,?,1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
            " domain=excluded.domain, name=excluded.name, enabled=excluded.enabled, active=1,"
            " search_url=excluded.search_url, position=excluded.position, updated_at=excluded.updated_at",
            (provider["id"], provider["domain"], provider["name"], int(provider["enabled"]),
             provider.get("search_url"), position, now, now))


def _catalog_providers(providers, *, include_disabled=False):
    from .recommendation_providers import configured_providers
    return [{**provider, "enabled": True} for provider in
            configured_providers(providers, enabled_only=not include_disabled)]


def _catalog_normalized(item, providers):
    from .recommendation_tools import canonical_video_url
    if not isinstance(item, dict):
        return None
    return canonical_video_url(item.get("source_url") or item.get("url"), providers)


def _catalog_text(value, maximum):
    return value.strip()[:maximum] if isinstance(value, str) else None


def _catalog_number(value, *, integer=False):
    if (type(value) not in (int, float) or value < 0
            or value > 2 ** 53 or not math.isfinite(value)):
        return None
    return int(value) if integer else value


def _catalog_upsert(conn, item, normalized, *, discovery=False):
    source, url, video_id = normalized
    previous = conn.execute("SELECT * FROM recommendation_videos WHERE source_url=?", (url,)).fetchone()
    now = _now()
    verified = item.get("verified") is True
    # Less trustworthy observations must not replace independently verified metadata.
    use_metadata = previous is None or not previous["verified"] or verified
    metadata = {
        "title": _catalog_text(item.get("title"), 500),
        "description": _catalog_text(item.get("description"), 10000),
        "uploader": _catalog_text(item.get("uploader"), 500),
        "thumbnail_url": _catalog_text(item.get("thumbnail_url"), 2048),
        "duration": _catalog_number(item.get("duration")),
        "view_count": _catalog_number(item.get("view_count"), integer=True),
        "uploaded_at": _catalog_text(item.get("uploaded_at"), 100),
        "verification": _catalog_text(item.get("verification"), 80),
    }
    if previous:
        for key in metadata:
            if not use_metadata or metadata[key] in (None, ""):
                metadata[key] = previous[key]
    conn.execute(
        "INSERT INTO recommendation_videos"
        " (website_id, source_url, provider_video_id, title, description, uploader, thumbnail_url,"
        " duration, view_count, uploaded_at, verified, verification, discovered_count, created_at,"
        " updated_at, last_discovered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(source_url) DO UPDATE SET website_id=excluded.website_id,"
        " provider_video_id=excluded.provider_video_id, title=excluded.title,"
        " description=excluded.description, uploader=excluded.uploader, thumbnail_url=excluded.thumbnail_url,"
        " duration=excluded.duration, view_count=excluded.view_count, uploaded_at=excluded.uploaded_at,"
        " verified=MAX(recommendation_videos.verified, excluded.verified), verification=excluded.verification,"
        " discovered_count=recommendation_videos.discovered_count+excluded.discovered_count,"
        " updated_at=excluded.updated_at,"
        " last_discovered_at=COALESCE(excluded.last_discovered_at, recommendation_videos.last_discovered_at)",
        (source, url, video_id, metadata["title"], metadata["description"], metadata["uploader"],
         metadata["thumbnail_url"], metadata["duration"], metadata["view_count"], metadata["uploaded_at"],
         int(verified), metadata["verification"], int(discovery), now, now, now if discovery else None))
    return conn.execute("SELECT id FROM recommendation_videos WHERE source_url=?", (url,)).fetchone()["id"]


def _catalog_origin(conn, catalog_id, origin):
    now = _now()
    conn.execute(
        "INSERT INTO recommendation_video_origins (video_id, origin, first_seen_at, last_seen_at)"
        " VALUES (?,?,?,?) ON CONFLICT(video_id, origin) DO UPDATE SET"
        " last_seen_at=excluded.last_seen_at, occurrence_count=occurrence_count+1",
        (catalog_id, origin, now, now))


def _catalog_payloads(conn, rows):
    if not rows:
        return []
    placeholders = ",".join("?" for _ in rows)
    origins = {}
    for entry in conn.execute(
            f"SELECT * FROM recommendation_video_origins WHERE video_id IN ({placeholders}) ORDER BY origin",
            [row["id"] for row in rows]):
        origins.setdefault(entry["video_id"], []).append(entry["origin"])
    ready_media = {}
    for entry in conn.execute(
            f"SELECT id, source_url FROM videos WHERE status='ready' AND source_url IN ({placeholders})"
            " ORDER BY created_at DESC", [row["source_url"] for row in rows]):
        ready_media.setdefault(entry["source_url"], entry["id"])
    return [{"id": row["provider_video_id"], "catalog_id": row["id"],
             "source": row["website_id"], "connector": row["website_id"], "source_url": row["source_url"],
             "media_id": ready_media.get(row["source_url"]),
             "title": row["user_title"] or row["title"] or row["source_url"],
             "description": row["user_description"] if row["user_description"] is not None else row["description"],
             "user_title": row["user_title"], "user_description": row["user_description"],
             "title_fetch_status": row["title_fetch_status"], "title_fetch_error": row["title_fetch_error"],
             "title_fetch_method": row["title_fetch_method"],
             "uploader": row["uploader"],
             "thumbnail_url": (f"/api/recommendations/watch-later/{row['id']}/thumbnail"
                               if row["thumbnail_path"] else row["thumbnail_url"]),
             "thumbnail_fetch_status": row["thumbnail_fetch_status"],
             "thumbnail_fetch_error": row["thumbnail_fetch_error"],
             "duration": row["duration"], "view_count": row["view_count"], "uploaded_at": row["uploaded_at"],
             "verified": bool(row["verified"]), "verification": row["verification"],
             "origins": origins.get(row["id"], []), "user_added": "watch_later" in origins.get(row["id"], []),
             **{key: row[key] for key in ("discovered_count", "recommended_count", "fallback_count",
                                        "created_at", "updated_at", "last_discovered_at", "last_recommended_at")}}
            for row in rows]


def store_discoveries(items, providers) -> None:
    """Persist internal search observations without changing preference revision."""
    configured = _catalog_providers(providers)
    with _LOCK, _connect() as conn:
        active = {row["id"] for row in conn.execute(
            "SELECT id FROM recommendation_websites WHERE active=1 AND enabled=1")}
        seen = set()
        for item in items:
            normalized = _catalog_normalized(item, configured)
            if not normalized or normalized[0] not in active or normalized[1] in seen:
                continue
            seen.add(normalized[1])
            catalog_id = _catalog_upsert(conn, item, normalized, discovery=True)
            origins = item.get("origins", [])
            origins = [value for value in origins if value in ("custom_search", "public_search")] if isinstance(origins, list) else []
            if not origins:
                origins = ["public_search" if item.get("verification") == "web_search" else "custom_search"]
            for origin in set(origins):
                _catalog_origin(conn, catalog_id, origin)


def list_catalog_candidates(providers, limit=600) -> list[dict]:
    configured = _catalog_providers(providers)
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("Catalog limit must be between 1 and 5000.")
    ids = [provider["id"] for provider in configured]
    if not ids:
        return []
    with _LOCK, _connect() as conn:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            "WITH eligible AS (SELECT v.*, CASE"
            " WHEN EXISTS (SELECT 1 FROM recommendation_video_origins o WHERE o.video_id=v.id AND o.origin='watch_later') THEN 0"
            " WHEN EXISTS (SELECT 1 FROM recommendation_video_origins o WHERE o.video_id=v.id AND o.origin='custom_search') THEN 1"
            " ELSE 2 END AS pool"
            " FROM recommendation_videos v JOIN recommendation_websites w ON w.id=v.website_id"
            f" WHERE w.active=1 AND w.enabled=1 AND w.id IN ({placeholders})"
            " AND EXISTS (SELECT 1 FROM recommendation_video_origins o WHERE o.video_id=v.id"
            " AND o.origin IN ('watch_later','custom_search','public_search'))),"
            " ranked AS (SELECT eligible.*, ROW_NUMBER() OVER (PARTITION BY pool"
            " ORDER BY COALESCE(last_recommended_at, '') ASC, updated_at DESC, id DESC) AS pool_rank FROM eligible)"
            " SELECT * FROM ranked ORDER BY pool_rank, pool LIMIT ?",
            (*ids, limit)).fetchall()
        return _catalog_payloads(conn, rows)


def record_recommendations(items, providers, fallback=False) -> None:
    """Record presentations separately from discovery and manual membership."""
    configured = _catalog_providers(providers)
    with _LOCK, _connect() as conn:
        active = {row["id"] for row in conn.execute(
            "SELECT id FROM recommendation_websites WHERE active=1 AND enabled=1")}
        seen = set()
        for item in items:
            normalized = _catalog_normalized(item, configured)
            if not normalized or normalized[0] not in active or normalized[1] in seen:
                continue
            seen.add(normalized[1])
            # Display metadata may contain a manual title/note override. Recording
            # a presentation must not overwrite the independent discovery record.
            existing = conn.execute("SELECT id FROM recommendation_videos WHERE source_url=?", (normalized[1],)).fetchone()
            catalog_id = existing["id"] if existing else _catalog_upsert(conn, item, normalized)
            if not conn.execute("SELECT 1 FROM recommendation_video_origins WHERE video_id=?", (catalog_id,)).fetchone():
                _catalog_origin(conn, catalog_id, "model")
            now = _now()
            conn.execute("UPDATE recommendation_videos SET recommended_count=recommended_count+1,"
                         " fallback_count=fallback_count+?, last_recommended_at=? WHERE id=?",
                         (int(bool(fallback)), now, catalog_id))
            conn.execute("INSERT INTO recommendation_history (video_id, recommended_at, fallback) VALUES (?,?,?)",
                         (catalog_id, now, int(bool(fallback))))


def add_watch_later(videos) -> dict:
    """Validate the whole manual import before atomically saving any membership."""
    validated = WatchLaterImport(videos=videos)
    with _LOCK, _connect() as conn:
        # The provider snapshot and all memberships commit as one write transaction,
        # including when another application process updates the same database.
        conn.execute("BEGIN IMMEDIATE")
        settings = _settings_payload(conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone())
        providers = _catalog_providers(settings["providers"], include_disabled=True)
        normalized_items = {}
        for video in validated.videos:
            item = video.model_dump(exclude_unset=True)
            normalized = _catalog_normalized(item, providers)
            if normalized is None:
                raise ValueError("Every saved URL must be an individual video on a configured website.")
            if normalized[1] in normalized_items:
                item = {**normalized_items[normalized[1]][0], **item}
            normalized_items[normalized[1]] = (item, normalized)
        added = updated = 0
        catalog_ids = []
        for item, normalized in normalized_items.values():
            existing = conn.execute("SELECT id FROM recommendation_videos WHERE source_url=?", (normalized[1],)).fetchone()
            catalog_id = (existing["id"] if existing else
                          _catalog_upsert(conn, {"verified": False, "verification": "user_added"}, normalized))
            conn.execute("UPDATE recommendation_videos SET updated_at=? WHERE id=?", (_now(), catalog_id))
            present = conn.execute("SELECT 1 FROM recommendation_video_origins WHERE video_id=? AND origin='watch_later'",
                                   (catalog_id,)).fetchone()
            added += int(present is None)
            updated += int(present is not None)
            _catalog_origin(conn, catalog_id, "watch_later")
            for key in ("title", "description"):
                if key in item:
                    conn.execute(f"UPDATE recommendation_videos SET user_{key}=? WHERE id=?", (item[key], catalog_id))
            catalog_ids.append(catalog_id)
        conn.execute("UPDATE recommendation_settings SET revision=revision+1 WHERE id=1")
        rows = [conn.execute("SELECT * FROM recommendation_videos WHERE id=?", (catalog_id,)).fetchone()
                for catalog_id in catalog_ids]
        return {"items": _catalog_payloads(conn, rows), "added": added, "updated": updated,
                "revision": settings["revision"] + 1}


def list_watch_later(source="all", limit=200, offset=0) -> dict:
    if (type(limit) is not int or not 1 <= limit <= 200
            or type(offset) is not int or not 0 <= offset <= 2 ** 63 - 1):
        raise ValueError("Watch-later pagination is invalid.")
    where = "o.origin='watch_later'"
    params = []
    if source != "all":
        where += " AND v.website_id=?"
        params.append(source)
    with _LOCK, _connect() as conn:
        query = " FROM recommendation_videos v JOIN recommendation_video_origins o ON o.video_id=v.id WHERE " + where
        total = conn.execute("SELECT COUNT(*)" + query, params).fetchone()[0]
        rows = conn.execute("SELECT v.*" + query + " ORDER BY o.last_seen_at DESC, v.id DESC LIMIT ? OFFSET ?",
                            (*params, limit, offset)).fetchall()
        revision = conn.execute("SELECT revision FROM recommendation_settings WHERE id=1").fetchone()[0]
        return {"items": _catalog_payloads(conn, rows), "total": total, "revision": revision}


def get_watch_later_item(catalog_id, *, title_fetch_token=None) -> dict | None:
    if type(catalog_id) is not int or not 1 <= catalog_id <= 2 ** 63 - 1:
        return None
    token_filter = ""
    parameters = [catalog_id]
    if title_fetch_token is not None:
        if not isinstance(title_fetch_token, str) or not title_fetch_token or len(title_fetch_token) > 128:
            return None
        token_filter = " AND v.title_fetch_status='pending' AND v.title_fetch_token=?"
        parameters.append(title_fetch_token)
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT v.* FROM recommendation_videos v JOIN recommendation_video_origins o ON o.video_id=v.id"
            " WHERE v.id=? AND o.origin='watch_later'" + token_filter, parameters).fetchone()
        return _catalog_payloads(conn, [row])[0] if row else None


def get_watch_later_by_source_url(source_url) -> dict | None:
    """Find a saved video by the exact or canonical URL used for a download."""
    if not isinstance(source_url, str) or not source_url or len(source_url) > 2048:
        return None
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT v.* FROM recommendation_videos v JOIN recommendation_video_origins o ON o.video_id=v.id"
            " WHERE v.source_url=? AND o.origin='watch_later'", (source_url,)).fetchone()
        if row is None:
            settings = _settings_payload(
                conn.execute("SELECT * FROM recommendation_settings WHERE id=1").fetchone())
            providers = _catalog_providers(settings["providers"], include_disabled=True)
            normalized = _catalog_normalized({"source_url": source_url}, providers)
            if normalized is not None:
                row = conn.execute(
                    "SELECT v.* FROM recommendation_videos v"
                    " JOIN recommendation_video_origins o ON o.video_id=v.id"
                    " WHERE v.source_url=? AND o.origin='watch_later'", (normalized[1],)).fetchone()
        return _catalog_payloads(conn, [row])[0] if row else None


def begin_watch_later_title(catalog_id, token, *, force=False) -> dict | None:
    """Claim one manual save without replacing an existing worker."""
    if not isinstance(token, str) or not token or len(token) > 128:
        raise ValueError("A nonempty title lookup token up to 128 characters is required.")
    if type(catalog_id) is not int or not 1 <= catalog_id <= 2 ** 63 - 1:
        return None
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT v.* FROM recommendation_videos v JOIN recommendation_video_origins o ON o.video_id=v.id"
            " JOIN recommendation_websites w ON w.id=v.website_id"
            " WHERE v.id=? AND o.origin='watch_later' AND w.active=1", (catalog_id,)).fetchone()
        if (row is None or row["title_fetch_status"] == "pending" or (not force and (
                (row["title"] or "").strip() or (row["user_title"] or "").strip()))):
            return None
        conn.execute("UPDATE recommendation_videos SET title_fetch_status='pending', title_fetch_error=NULL,"
                     " title_fetch_token=?, title_fetch_method=NULL WHERE id=?", (token, catalog_id))
        row = conn.execute("SELECT * FROM recommendation_videos WHERE id=?", (catalog_id,)).fetchone()
        return _catalog_payloads(conn, [row])[0]


def finish_watch_later_title(catalog_id, token, title=None, error=None, method=None, *, force=False,
                             expected_title=None) -> bool:
    """Complete only the still-owned manual save, preserving discovery metadata."""
    if (type(catalog_id) is not int or not 1 <= catalog_id <= 2 ** 63 - 1
            or not isinstance(token, str) or not token or len(token) > 128):
        return False
    title = _catalog_text(title, 500)
    error = _catalog_text(error, 500)
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT v.* FROM recommendation_videos v JOIN recommendation_video_origins o ON o.video_id=v.id"
            " WHERE v.id=? AND o.origin='watch_later' AND v.title_fetch_status='pending'"
            " AND v.title_fetch_token=?", (catalog_id, token)).fetchone()
        if row is None:
            return False
        changed = bool(title and (force or not (row["title"] or "").strip()))
        if changed:
            clear_user_title = bool(force and row["user_title"] and row["user_title"] == expected_title)
            conn.execute("UPDATE recommendation_videos SET title=?, user_title=CASE WHEN ? THEN NULL ELSE user_title END,"
                         " updated_at=? WHERE id=?", (title, int(clear_user_title), _now(), catalog_id))
            conn.execute("UPDATE recommendation_settings SET revision=revision+1 WHERE id=1")
        ready = changed or (not force and bool((row["title"] or "").strip() or (row["user_title"] or "").strip()))
        conn.execute("UPDATE recommendation_videos SET title_fetch_status=?, title_fetch_error=?,"
                     " title_fetch_token=NULL, title_fetch_method=? WHERE id=?",
                     ("ready" if ready else "unavailable", None if ready else error or "The video title could not be retrieved.",
                      method if changed and method in ("source", "ai") else None, catalog_id))
        return True


def begin_watch_later_thumbnail(catalog_id, token) -> dict | None:
    if (type(catalog_id) is not int or not 1 <= catalog_id <= 2 ** 63 - 1
            or not isinstance(token, str) or not token or len(token) > 128):
        return None
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT v.* FROM recommendation_videos v JOIN recommendation_video_origins o ON o.video_id=v.id"
            " WHERE v.id=? AND o.origin='watch_later'", (catalog_id,)).fetchone()
        if row is None or row["thumbnail_path"] or row["thumbnail_fetch_status"] == "pending":
            return None
        conn.execute("UPDATE recommendation_videos SET thumbnail_fetch_status='pending',"
                     " thumbnail_fetch_error=NULL, thumbnail_fetch_token=? WHERE id=?", (token, catalog_id))
        row = conn.execute("SELECT * FROM recommendation_videos WHERE id=?", (catalog_id,)).fetchone()
        return _catalog_payloads(conn, [row])[0]


def finish_watch_later_thumbnail(catalog_id, token, path=None, error=None) -> bool:
    path = _catalog_text(path, 4096)
    error = _catalog_text(error, 500)
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT v.id FROM recommendation_videos v JOIN recommendation_video_origins o ON o.video_id=v.id"
            " WHERE v.id=? AND o.origin='watch_later' AND v.thumbnail_fetch_status='pending'"
            " AND v.thumbnail_fetch_token=?", (catalog_id, token)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE recommendation_videos SET thumbnail_path=?, thumbnail_fetch_status=?,"
                     " thumbnail_fetch_error=?, thumbnail_fetch_token=NULL WHERE id=?",
                     (path, "ready" if path else "unavailable",
                      None if path else error or "A thumbnail could not be downloaded.", catalog_id))
        return True


def get_watch_later_thumbnail(catalog_id) -> str | None:
    if type(catalog_id) is not int or not 1 <= catalog_id <= 2 ** 63 - 1:
        return None
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT v.thumbnail_path FROM recommendation_videos v"
            " JOIN recommendation_video_origins o ON o.video_id=v.id"
            " WHERE v.id=? AND o.origin='watch_later'", (catalog_id,)).fetchone()
        return row["thumbnail_path"] if row else None


def save_watch_later_links(source_page, links) -> dict:
    if not isinstance(source_page, str) or not source_page or len(source_page) > 2048 or not isinstance(links, list):
        raise ValueError("The page-link import is invalid.")
    if not 1 <= len(links) <= 500:
        raise ValueError("A page must provide between 1 and 500 links.")
    added = updated = 0
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in links:
            if (not isinstance(item, dict) or not isinstance(item.get("url"), str)
                    or not item["url"] or len(item["url"]) > 2048):
                raise ValueError("A page link is invalid.")
            title = _catalog_text(item.get("title"), 500)
            existing = conn.execute("SELECT id FROM watch_later_links WHERE url=?", (item["url"],)).fetchone()
            if existing:
                conn.execute("UPDATE watch_later_links SET title=COALESCE(?,title), source_page=?, updated_at=? WHERE id=?",
                             (title, source_page, _now(), existing["id"]))
                updated += 1
            else:
                now = _now()
                conn.execute("INSERT INTO watch_later_links (url,title,source_page,created_at,updated_at)"
                             " VALUES (?,?,?,?,?)", (item["url"], title, source_page, now, now))
                added += 1
        total = conn.execute("SELECT COUNT(*) FROM watch_later_links").fetchone()[0]
    return {"added": added, "updated": updated, "total": total}


def list_watch_later_links(limit=200, offset=0) -> dict:
    if (type(limit) is not int or not 1 <= limit <= 200
            or type(offset) is not int or not 0 <= offset <= 2 ** 63 - 1):
        raise ValueError("Saved-link pagination is invalid.")
    with _LOCK, _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM watch_later_links").fetchone()[0]
        rows = conn.execute("SELECT * FROM watch_later_links ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
    return {"items": [dict(row) for row in rows], "total": total}


def remove_watch_later_link(link_id) -> dict:
    if type(link_id) is not int or not 1 <= link_id <= 2 ** 63 - 1:
        return {"removed": False}
    with _LOCK, _connect() as conn:
        removed = conn.execute("DELETE FROM watch_later_links WHERE id=?", (link_id,)).rowcount > 0
    return {"removed": removed}


def remove_watch_later(catalog_id: int) -> dict:
    if type(catalog_id) is not int or not 1 <= catalog_id <= 2 ** 63 - 1:
        raise ValueError("Catalog ID must be a positive SQLite integer.")
    with _LOCK, _connect() as conn:
        removed = conn.execute("DELETE FROM recommendation_video_origins WHERE video_id=? AND origin='watch_later'",
                               (catalog_id,)).rowcount > 0
        if removed:
            conn.execute("UPDATE recommendation_videos SET user_title=NULL, user_description=NULL,"
                         " title_fetch_status='idle', title_fetch_error=NULL, title_fetch_token=NULL,"
                         " title_fetch_method=NULL, thumbnail_path=NULL, thumbnail_fetch_status='idle',"
                         " thumbnail_fetch_error=NULL, thumbnail_fetch_token=NULL, updated_at=? WHERE id=?",
                         (_now(), catalog_id))
            conn.execute("UPDATE recommendation_settings SET revision=revision+1 WHERE id=1")
        revision = conn.execute("SELECT revision FROM recommendation_settings WHERE id=1").fetchone()[0]
        return {"removed": removed, "revision": revision}


def fail_stale_videos() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE videos SET status = 'failed', error_message = ?,"
            " completed_at = ?"
            " WHERE status IN ('queued', 'downloading', 'processing')",
            ("Server restarted while the download was running.", _now()),
        )
        conn.execute(
            "UPDATE media_conversions SET status = 'failed', error_message = ?,"
            " completed_at = ? WHERE status IN ('queued', 'converting')",
            ("Server restarted while the conversion was running.", _now()),
        )


def _decode(job: dict) -> dict:
    for key in ("urls", "params"):
        if job.get(key):
            try:
                job[key] = json.loads(job[key])
            except (ValueError, TypeError):
                pass
    return job
