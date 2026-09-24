"""Durable Connector configuration and activity delivery outbox.

Playback reports contain elapsed viewing time, not the seek position. Reports are
aggregated transactionally before delivery so network retries never add viewing
time again. This module performs no network requests.
"""
import json
import math
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from .. import config
from . import db


_SCHEMA = """
CREATE TABLE IF NOT EXISTS connector_integration (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    base_url TEXT NOT NULL,
    token TEXT NOT NULL,
    session_id TEXT,
    connector_url TEXT NOT NULL DEFAULT '',
    registered_tools TEXT NOT NULL DEFAULT '[]',
    last_error TEXT,
    last_sync_at TEXT
);
CREATE TABLE IF NOT EXISTS connector_activity_events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS connector_activity_pending
    ON connector_activity_events(delivered_at, created_at);
CREATE TABLE IF NOT EXISTS connector_watch_activity (
    video_id TEXT PRIMARY KEY,
    metadata TEXT NOT NULL,
    total_watched_seconds REAL NOT NULL DEFAULT 0,
    pending_watched_seconds REAL NOT NULL DEFAULT 0,
    position_seconds REAL NOT NULL DEFAULT 0,
    pending_since TEXT,
    last_watched_at TEXT NOT NULL,
    completion_reported INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS connector_watch_receipts (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connector_watched_tags (
    video_id TEXT NOT NULL,
    keyword TEXT NOT NULL COLLATE NOCASE,
    watched_seconds REAL NOT NULL DEFAULT 0,
    last_watched_at TEXT NOT NULL,
    PRIMARY KEY (video_id, keyword)
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    """Create integration tables without replacing existing settings or events."""
    config.JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db._LOCK, db._connect() as conn:
        conn.executescript(_SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(connector_integration)")}
        if "registered_tools" not in columns:
            conn.execute("ALTER TABLE connector_integration ADD COLUMN registered_tools"
                         " TEXT NOT NULL DEFAULT '[]'")
        conn.execute(
            "INSERT OR IGNORE INTO connector_integration (id,base_url,token) VALUES (1,?,?)",
            ((getattr(config, "CONNECTOR_PUBLIC_URL", None)
              or os.environ.get("CONNECTOR_APP_BASE_URL")
              or f"http://127.0.0.1:{config.BACKEND_PORT}").rstrip("/"),
             secrets.token_urlsafe(32)),
        )


def _state(conn):
    row = conn.execute("SELECT * FROM connector_integration WHERE id=1").fetchone()
    result = dict(row)
    result.pop("id", None)
    result["enabled"] = bool(result["enabled"])
    result["registered_tools"] = json.loads(result["registered_tools"])
    return result


def get_state():
    with db._LOCK, db._connect() as conn:
        return _state(conn)


def update_state(**fields):
    allowed = {"enabled", "base_url", "token", "session_id", "connector_url",
               "last_error", "last_sync_at", "registered_tools"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError("Unknown Connector state fields: " + ", ".join(sorted(unknown)))
    if "registered_tools" in fields:
        fields["registered_tools"] = json.dumps(fields["registered_tools"])
    with db._LOCK, db._connect() as conn:
        if fields:
            conn.execute("UPDATE connector_integration SET "
                         + ", ".join(f"{key}=?" for key in fields) + " WHERE id=1",
                         tuple(fields.values()))
        return _state(conn)


def _insert_event(conn, event_type, payload, event_id=None, created_at=None):
    if not _state(conn)["enabled"]:
        return None
    event_id = event_id or str(uuid.uuid4())
    serialized = json.dumps(_clean_payload(payload), ensure_ascii=False, allow_nan=False)
    if len(serialized.encode("utf-8")) > 256_000:
        raise ValueError("Activity payload exceeds 256 KB.")
    conn.execute(
        "INSERT OR IGNORE INTO connector_activity_events (id,type,payload,created_at)"
        " VALUES (?,?,?,?)",
        (event_id, event_type, serialized, created_at or _now()),
    )
    return event_id


def _clean_payload(value, depth=0):
    if depth > 12:
        raise ValueError("Activity payload is too deeply nested.")
    if isinstance(value, str):
        return "".join(char for char in value if ord(char) >= 32 or char in "\n\r\t")[:16000]
    if isinstance(value, dict):
        if len(value) > 200:
            raise ValueError("Activity payload has too many fields.")
        return {str(key)[:100]: _clean_payload(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 200:
            raise ValueError("Activity payload has too many items.")
        return [_clean_payload(item, depth + 1) for item in value]
    return value


def enqueue(event_type, payload, event_id=None):
    with db._LOCK, db._connect() as conn:
        return _insert_event(conn, event_type, payload, event_id)


def _event_payload(row):
    result = dict(row)
    result["payload"] = json.loads(result["payload"])
    return result


def pending_events(limit=20):
    with db._LOCK, db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM connector_activity_events WHERE delivered_at IS NULL"
            " ORDER BY created_at, rowid LIMIT ?", (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [_event_payload(row) for row in rows]


def mark_delivered(event_id):
    with db._LOCK, db._connect() as conn:
        conn.execute("UPDATE connector_activity_events SET delivered_at=?,"
                     " attempts=attempts+1,last_error=NULL WHERE id=? AND delivered_at IS NULL",
                     (_now(), event_id))


def mark_failed(event_id, error):
    with db._LOCK, db._connect() as conn:
        conn.execute("UPDATE connector_activity_events SET attempts=attempts+1,last_error=?"
                     " WHERE id=? AND delivered_at IS NULL", (str(error)[:1000], event_id))


def status():
    with db._LOCK, db._connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(delivered_at IS NULL),0) AS pending,"
            " COALESCE(SUM(delivered_at IS NOT NULL),0) AS delivered"
            " FROM connector_activity_events",
        ).fetchone()
        return dict(row)


def list_events(limit=50, offset=0, event_type=None, since=None, until=None):
    clauses, args = [], []
    for column, operator, value in (("type", "=", event_type),
                                     ("created_at", ">=", since),
                                     ("created_at", "<=", until)):
        if value is not None:
            clauses.append(f"{column}{operator}?")
            args.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    with db._LOCK, db._connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM connector_activity_events" + where,
                             args).fetchone()[0]
        rows = conn.execute("SELECT * FROM connector_activity_events" + where
                            + " ORDER BY created_at, rowid LIMIT ? OFFSET ?",
                            [*args, limit, offset]).fetchall()
    return {"items": [_event_payload(row) for row in rows], "total": total,
            "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < total else None}


def record_search(query, source, result_count, session_id=None, event_id=None):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
        raise ValueError("Search activity requires a query of 1 to 2000 characters.")
    if not isinstance(source, str) or not 1 <= len(source) <= 253:
        raise ValueError("Search activity requires a valid source.")
    if type(result_count) is not int or not 0 <= result_count <= 1_000_000:
        raise ValueError("Search result count must be between zero and one million.")
    if session_id is not None:
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 200:
            raise ValueError("Search activity requires a valid session id.")
        # Loading more results is one search intent, including after a restart.
        # A new UI search session deliberately produces a new observation.
        identity = json.dumps([session_id, " ".join(query.split()).casefold(), source],
                              ensure_ascii=False)
        event_id = "search-" + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
    return enqueue("search", {"query": query, "source": source,
                              "result_count": result_count, "session_id": session_id},
                   event_id=event_id)


def record_impressions(items, context, event_id=None):
    """Store the actual cards displayed, with their metadata at display time."""
    return enqueue("recommendation_impressions", {"items": items, "context": context},
                   event_id=event_id)


def _recent_tags(conn, limit=30, since=None):
    since = since or (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = conn.execute(
        "SELECT keyword, SUM(watched_seconds) AS watched_seconds, COUNT(*) AS video_count,"
        " MAX(last_watched_at) AS last_watched_at FROM connector_watched_tags"
        " WHERE last_watched_at>=? GROUP BY keyword COLLATE NOCASE"
        " ORDER BY last_watched_at DESC, watched_seconds DESC, keyword LIMIT ?",
        (since, max(1, min(int(limit), 200))),
    ).fetchall()
    return [dict(row) for row in rows]


def recent_tags(limit=30, since=None):
    """Recent video/tag pairs, with their lifetime accumulated watching totals.

    ``since`` filters when a pair was last watched, not the time contributing to
    its total. The result therefore describes recent interests, not a time-window
    viewing report.
    """
    with db._LOCK, db._connect() as conn:
        return _recent_tags(conn, limit, since)


def _emit_watch(conn, video_id, completed=False):
    watch = conn.execute("SELECT * FROM connector_watch_activity WHERE video_id=?",
                         (video_id,)).fetchone()
    payload = {**json.loads(watch["metadata"]),
               "watched_seconds": watch["pending_watched_seconds"],
               "total_watched_seconds": watch["total_watched_seconds"],
               "position_seconds": watch["position_seconds"],
               "completed": completed, "finished": completed,
               "status": "finished" if completed else "watching",
               "last_watched_at": watch["last_watched_at"],
               "recent_tags": _recent_tags(conn)}
    event_id = _insert_event(conn, "watch", payload)
    conn.execute("UPDATE connector_watch_activity SET pending_watched_seconds=0,"
                 " pending_since=NULL,completion_reported=? WHERE video_id=?",
                 (int(completed), video_id))
    return event_id


def record_watch(row, watched_seconds, position_seconds, completed, event_id=None):
    """Accumulate real elapsed seconds; emit every minute or upon finishing.

    Use a stable event_id when retrying the same report. A fresh play after a
    completion starts a new completion cycle, while duplicate ended callbacks do
    not create repeated finished events.
    """
    watched_seconds, position_seconds = float(watched_seconds), float(position_seconds)
    if (not math.isfinite(watched_seconds) or not math.isfinite(position_seconds)
            or watched_seconds < 0 or position_seconds < 0):
        raise ValueError("Playback seconds must be finite and nonnegative.")
    video_id = row.get("video_id") or row.get("id")
    if not video_id:
        raise ValueError("Playback report requires a video id.")
    now = _now()
    with db._LOCK, db._connect() as conn:
        if not _state(conn)["enabled"]:
            return None
        if event_id:
            receipt = conn.execute(
                "INSERT OR IGNORE INTO connector_watch_receipts (id,created_at) VALUES (?,?)",
                (event_id, now),
            )
            if not receipt.rowcount:
                return None
        existing = conn.execute("SELECT * FROM connector_watch_activity WHERE video_id=?",
                                (video_id,)).fetchone()
        if watched_seconds == 0 and not completed:
            return None
        if existing and existing["completion_reported"] and completed and watched_seconds == 0:
            return None
        keywords = [item["keyword"] for item in conn.execute(
            "SELECT keyword FROM video_keywords WHERE video_id=? ORDER BY position,keyword",
            (video_id,),
        ).fetchall()]
        if not keywords:
            keywords = row.get("keywords") or []
        metadata = {key: row.get(key) for key in
                    ("source_url", "title", "uploader", "connector", "duration")}
        metadata.update(video_id=video_id, keywords=keywords)
        conn.execute(
            "INSERT INTO connector_watch_activity (video_id,metadata,total_watched_seconds,"
            " pending_watched_seconds,position_seconds,pending_since,last_watched_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET"
            " metadata=excluded.metadata,"
            " total_watched_seconds=connector_watch_activity.total_watched_seconds"
            " +excluded.total_watched_seconds,"
            " pending_watched_seconds=connector_watch_activity.pending_watched_seconds"
            " +excluded.pending_watched_seconds,position_seconds=excluded.position_seconds,"
            " pending_since=COALESCE(connector_watch_activity.pending_since,excluded.pending_since),"
            " last_watched_at=excluded.last_watched_at,completion_reported=0",
            (video_id, json.dumps(metadata, ensure_ascii=False), watched_seconds,
             watched_seconds, position_seconds, now, now),
        )
        if watched_seconds > 0:
            for keyword in dict.fromkeys(keywords):
                conn.execute(
                    "INSERT INTO connector_watched_tags (video_id,keyword,watched_seconds,"
                    " last_watched_at) VALUES (?,?,?,?) ON CONFLICT(video_id,keyword) DO UPDATE SET"
                    " watched_seconds=connector_watched_tags.watched_seconds+excluded.watched_seconds,"
                    " last_watched_at=excluded.last_watched_at",
                    (video_id, keyword, watched_seconds, now),
                )
        pending = conn.execute("SELECT pending_watched_seconds FROM connector_watch_activity"
                               " WHERE video_id=?", (video_id,)).fetchone()[0]
        if completed or pending >= 60:
            return _emit_watch(conn, video_id, completed)
    return None


def flush_watches(force=False):
    """Flush persisted short visits after a minute, or on graceful shutdown."""
    with db._LOCK, db._connect() as conn:
        if not _state(conn)["enabled"]:
            return []
        cutoff = (datetime.fromisoformat(_now()) - timedelta(seconds=60)).isoformat()
        rows = conn.execute(
            "SELECT video_id FROM connector_watch_activity WHERE pending_watched_seconds>0"
            + ("" if force else " AND pending_since<=?"), () if force else (cutoff,),
        ).fetchall()
        return [_emit_watch(conn, row["video_id"]) for row in rows]
