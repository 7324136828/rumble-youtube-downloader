"""Bounded agent tools shared by the authenticated Connector callbacks.

Remote search/recommendation calls run as short-lived background tasks because
The Connector's webhook timeout is ten seconds. Task results survive for thirty
minutes in this process; catalog/history and queued downloads remain in SQLite.
"""
import copy
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from .. import config
from ..connectors import registry
from ..schemas import connector_tools as args
from ..schemas.recommendations import RecommendationSettingsPatch
from . import (db, library, manual_video_search, recommendation_tools, recommendations,
               watch_later_thumbnails, watch_later_titles)

_LOG = logging.getLogger(__name__)
_DOWNLOAD_LOCK = threading.Lock()
_TASK_LOCK = threading.Lock()
_TASKS = {}
_TASK_TTL = 30 * 60
_MAX_TASKS = 128
_MAX_RUNNING = 4


def _absolute(value, base_url):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        return None
    if value.startswith("/api/"):
        return base_url.rstrip("/") + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None


def _local_video(row, base_url):
    """Use an allowlist: internal paths and downloader diagnostics stay private."""
    keys = ("id", "source_url", "connector", "status", "progress", "stage", "quality", "title",
            "uploader", "description", "duration", "width", "height", "file_size", "created_at",
            "completed_at", "expires_at")
    item = {key: row.get(key) for key in keys}
    item["downloaded_date"] = row.get("completed_at")
    item["keywords"] = db.get_video_keywords(row["id"])
    ready = row.get("status") == "ready" and bool(row.get("file_path")) and Path(row["file_path"]).is_file()
    item["playable"] = ready
    root = base_url.rstrip("/") + "/api/media/" + quote(row["id"], safe="")
    item["stream_url"] = root + "/stream" if ready else None
    item["download_url"] = root + "/download" if ready else None
    item["thumbnail_url"] = root + "/thumbnail" if row.get("thumbnail_path") else None
    return item


def _enrich(items, base_url):
    """Attach stream links only when a ready local media file really exists."""
    result = []
    for raw in items:
        item = dict(raw)
        source_url = item.get("source_url")
        with db._LOCK, db._connect() as conn:
            media = conn.execute("SELECT * FROM videos WHERE source_url=? AND status='ready'"
                                 " ORDER BY completed_at DESC, id DESC LIMIT 1", (source_url,)).fetchone()
        local = _local_video(dict(media), base_url) if media else None
        item["playable"] = bool(local and local["playable"])
        item["stream_url"] = local["stream_url"] if local else None
        item["media_id"] = local["id"] if local else None
        item["downloaded_date"] = local["downloaded_date"] if local else None
        item["thumbnail_url"] = (local["thumbnail_url"] if local and local["thumbnail_url"] else
                                 _absolute(item.get("thumbnail_url"), base_url))
        # A constant alt label avoids allowing scraped titles to inject Markdown.
        if item["thumbnail_url"]:
            thumbnail = quote(item["thumbnail_url"], safe=":/?=&%#@+;,")
            item["thumbnail_markdown"] = f"![Video thumbnail](<{thumbnail}>)"
        result.append(item)
    return result


def _with_players(payload, items):
    players = [{"url": item["stream_url"], "thumbnail": item.get("thumbnail_url"),
                "title": item.get("title"), "description": item.get("description"),
                "uploader": item.get("uploader"), "downloaded_date": item.get("downloaded_date")}
               for item in items if item.get("playable") and item.get("stream_url")]
    if players:
        # Backticks inside a scraped title must never terminate the fenced block.
        payload["video_markdown"] = "\n\n".join(
            "```video\n" + json.dumps(players[index:index + 20], ensure_ascii=False).replace("`", "\\u0060") + "\n```"
            for index in range(0, len(players), 20))
    return payload


def _page(items, total, request, **extra):
    next_offset = request.offset + len(items)
    return {"items": items, "total": total, "limit": request.limit, "offset": request.offset,
            "next_offset": next_offset if next_offset < total else None,
            "order": request.order, **extra}


def _filters(request, alias, date_column, *, catalog=False, description=True):
    clauses, params = [], []
    source_column = "website_id" if catalog else "connector"
    if request.source != "all":
        clauses.append(f"{alias}.{source_column}=?")
        params.append(request.source)
    title = f"COALESCE({alias}.user_title,{alias}.title,'')" if catalog else f"COALESCE({alias}.title,'')"
    description_column = (f"COALESCE({alias}.user_description,{alias}.description,'')" if catalog
                          else f"COALESCE({alias}.description,'')")
    if request.query:
        columns = [title, f"COALESCE({alias}.uploader,'')"]
        if description:
            columns.append(description_column)
        clauses.append("(" + " OR ".join(f"instr(lower({column}),lower(?))>0" for column in columns) + ")")
        params.extend([request.query] * len(columns))
    if request.uploader:
        clauses.append(f"instr(lower(COALESCE({alias}.uploader,'')),lower(?))>0")
        params.append(request.uploader)
    for value, operation in ((request.date_from, ">="), (request.date_to, "<=")):
        if value:
            clauses.append(f"julianday({date_column}) {operation} julianday(?)")
            params.append(value)
    return clauses, params


def _list_downloaded(request, base_url):
    date_column = "COALESCE(v.completed_at,v.created_at)"
    clauses, params = _filters(request, "v", date_column)
    clauses.insert(0, "v.status='ready'")
    if request.keyword:
        clauses.append("EXISTS (SELECT 1 FROM video_keywords k WHERE k.video_id=v.id AND k.keyword=? COLLATE NOCASE)")
        params.append(request.keyword)
    where = " AND ".join(clauses)
    direction = "ASC" if request.order == "oldest" else "DESC"
    with db._LOCK, db._connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM videos v WHERE {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT v.* FROM videos v WHERE {where} ORDER BY {date_column} {direction}, v.id {direction}"
                            " LIMIT ? OFFSET ?", [*params, request.limit, request.offset]).fetchall()
    items = [_local_video(dict(row), base_url) for row in rows]
    return _with_players(_page(items, total, request, date_field="downloaded_date"), items)


def _list_catalog(request, base_url, *, history=False):
    date_column = "h.recommended_at" if history else "o.first_seen_at"
    clauses, params = _filters(request, "v", date_column, catalog=True)
    if history:
        table = "recommendation_history h JOIN recommendation_videos v ON v.id=h.video_id"
        extra = "h.id AS history_id, h.recommended_at, h.fallback"
        tiebreaker = "h.id"
        if request.fallback is not None:
            clauses.append("h.fallback=?")
            params.append(int(request.fallback))
    else:
        table = "recommendation_video_origins o JOIN recommendation_videos v ON v.id=o.video_id"
        clauses.insert(0, "o.origin='watch_later'")
        extra = "o.first_seen_at AS saved_at"
        tiebreaker = "v.id"
    where = " AND ".join(clauses) or "1=1"
    direction = "ASC" if request.order == "oldest" else "DESC"
    with db._LOCK, db._connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT v.*, {extra} FROM {table} WHERE {where}"
                            f" ORDER BY {date_column} {direction}, {tiebreaker} {direction} LIMIT ? OFFSET ?",
                            [*params, request.limit, request.offset]).fetchall()
        items = db._catalog_payloads(conn, rows)
        for item, row in zip(items, rows):
            if history:
                item.update(history_id=row["history_id"], recommended_at=row["recommended_at"], fallback=bool(row["fallback"]))
            else:
                item["saved_at"] = row["saved_at"]
            # Fetch errors can contain downloader diagnostics; these are not catalog metadata.
            item.pop("title_fetch_error", None)
            item.pop("thumbnail_fetch_error", None)
    items = _enrich(items, base_url)
    result = _page(items, total, request, date_field="recommended_at" if history else "saved_at")
    if history:
        result["history_kind"] = "generated_recommendations"
    return _with_players(result, items)


def _list_watched(request, base_url):
    clauses, params = _filters(request, "h", "h.last_watched_at", description=False)
    clauses.insert(0, "h.watched_seconds>0")
    if request.completed is not None:
        clauses.append("h.completed=?")
        params.append(int(request.completed))
    where = " AND ".join(clauses)
    direction = "ASC" if request.order == "oldest" else "DESC"
    with db._LOCK, db._connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM watch_history h WHERE {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT h.* FROM watch_history h WHERE {where}"
                            f" ORDER BY h.last_watched_at {direction}, h.video_id {direction} LIMIT ? OFFSET ?",
                            [*params, request.limit, request.offset]).fetchall()
    items = _enrich([{**dict(row), "completed": bool(row["completed"])} for row in rows], base_url)
    return _with_players(_page(items, total, request, date_field="last_watched_at",
                              history_kind="per_video_watch_totals"), items)


def _canonical(url):
    normalized = recommendation_tools.canonical_video_url(url)
    return normalized[1] if normalized else urlunsplit(urlsplit(url)._replace(fragment=""))


def _download(request, base_url):
    # Validate the complete batch before creating a directory or starting a worker.
    urls = list(dict.fromkeys(_canonical(url) for url in request.urls))
    if any(registry.resolve(url) is None for url in urls):
        raise ValueError("A download URL has no supported connector")
    items = []
    with _DOWNLOAD_LOCK:
        for url in urls:
            with db._LOCK, db._connect() as conn:
                row = conn.execute("SELECT * FROM videos WHERE source_url=? AND status IN ('ready','queued','downloading','processing')"
                                   " ORDER BY CASE WHEN status='ready' THEN 0 ELSE 1 END, created_at DESC LIMIT 1", (url,)).fetchone()
            reused = row is not None
            row = dict(row) if reused else library.start_download(url, request.quality)
            items.append({**_local_video(row, base_url), "reused": reused})
    return _with_players({"items": items, "status": "accepted",
                          "next_tool": "clipfeed_get_download_status"}, items)


def _download_status(request, base_url):
    items, missing = [], []
    for video_id in dict.fromkeys(request.video_ids):
        row = db.get_video(video_id)
        if row is None:
            missing.append(video_id)
        else:
            items.append(_local_video(row, base_url))
    return _with_players({"items": items, "missing_video_ids": missing}, items)


def _hint(request, base_url):
    settings = recommendations.update_settings(RecommendationSettingsPatch(custom_prompt=request.hint))
    return {"custom_prompt": settings["custom_prompt"], "enabled": settings["enabled"], "revision": settings["revision"]}


def _add_watch_later(request, base_url):
    """Atomically save validated videos, then schedule optional metadata work."""
    videos = [video.model_dump(exclude_none=True) for video in request.videos]
    result = db.add_watch_later(videos)
    result["items"] = (watch_later_titles.schedule_items(result["items"])
                       if request.fetch_titles else
                       watch_later_thumbnails.schedule_items(result["items"]))
    result["items"] = _enrich(result["items"], base_url)
    return _with_players(result, result["items"])


def _search(request, base_url):
    result = manual_video_search.search_videos(request.query, request.source, request.limit, request.session_id).model_dump()
    result["results"] = _enrich(result["results"], base_url)
    return _with_players(result, result["results"])


def _recommend(request, base_url):
    result = recommendations.recommend(request)
    result["items"] = _enrich(result.get("items", []), base_url)
    return _with_players(result, result["items"])


def _prune_tasks():
    now = time.monotonic()
    for task_id in list(_TASKS):
        task = _TASKS[task_id]
        if task["status"] != "pending" and now - task["updated"] >= _TASK_TTL:
            del _TASKS[task_id]


def _task_payload(task_id, task):
    result = {key: copy.deepcopy(value) for key, value in task.items() if key != "updated"}
    result["task_id"] = task_id
    if result["status"] == "pending":
        result.update(next_tool="clipfeed_get_task", poll_after_seconds=2)
    return result


def _submit(name, handler, request, base_url):
    database_path = config.JOBS_DB_PATH
    with _TASK_LOCK:
        _prune_tasks()
        if sum(task["status"] == "pending" for task in _TASKS.values()) >= _MAX_RUNNING:
            raise ValueError("Four Connector searches or recommendation tasks are already running; poll them before starting another")
        if len(_TASKS) >= _MAX_TASKS:
            oldest = next(key for key, task in _TASKS.items() if task["status"] != "pending")
            del _TASKS[oldest]
        task_id = uuid.uuid4().hex
        task = {"tool": name, "status": "pending", "updated": time.monotonic()}
        _TASKS[task_id] = task

    def run():
        try:
            if config.JOBS_DB_PATH != database_path:
                raise RuntimeError("The task's database changed before it started")
            result = handler(request, base_url)
            if config.JOBS_DB_PATH != database_path:
                raise RuntimeError("The task's database changed while it was running")
            outcome = {"status": "completed", "result": result}
        except Exception:
            _LOG.exception("Connector tool %s failed", name)
            outcome = {"status": "failed", "error": "The operation failed. Check the downloader's settings and server log, then retry."}
        with _TASK_LOCK:
            if task_id in _TASKS:
                task.update(outcome, updated=time.monotonic())

    threading.Thread(target=run, name=f"connector-tool-{task_id[:8]}", daemon=True).start()
    # Always return the same submission shape, even for a very fast operation.
    return {"task_id": task_id, "tool": name, "status": "pending", "next_tool": "clipfeed_get_task",
            "poll_after_seconds": 2, "result_retention_seconds": _TASK_TTL}


def _get_task(request, base_url):
    with _TASK_LOCK:
        _prune_tasks()
        task = _TASKS.get(request.task_id)
        if task is None:
            raise LookupError("Task not found or expired; task results expire after 30 minutes or a server restart")
        return _task_payload(request.task_id, task)


def _recent_tags(request, base_url):
    from . import connector_activity
    return {"items": connector_activity.recent_tags(limit=request.limit),
            "recent_days": 30, "duration_kind": "cumulative_seconds_for_recently_watched_videos"}


def _list_activity(request, base_url):
    from . import connector_activity
    result = connector_activity.list_events(limit=request.limit, offset=request.offset,
                                           event_type=request.event_type, since=request.date_from,
                                           until=request.date_to)
    result["items"] = [{key: item[key] for key in ("id", "type", "payload", "created_at")}
                       for item in result["items"]]
    return result


_TOOLS = {
    "clipfeed_search_videos": (args.SearchVideos, _search,
        "Search enabled video websites with thumbnails and local playable videos. Returns a background task ID; poll clipfeed_get_task for results. Platform watch pages are not streams. Preserve returned video_markdown blocks in the reply."),
    "clipfeed_download_videos": (args.DownloadVideos, _download,
        "Queue up to 10 public video URLs for download using the user's download settings. Reuses existing ready or active downloads. Poll clipfeed_get_download_status using returned video IDs."),
    "clipfeed_get_download_status": (args.VideoIds, _download_status,
        "Get download progress, full video metadata and local stream URLs by video ID. Preserve returned video_markdown blocks to play ready downloads in chat."),
    "clipfeed_set_recommendation_hint": (args.SetHint, _hint,
        "Replace the user's saved recommendation hint at any time. Empty hint clears it. This does not enable recommendations or change other preferences."),
    "clipfeed_list_downloaded_videos": (args.DownloadedVideos, _list_downloaded,
        "Browse all downloaded videos from the beginning with title, uploader, description, download date, keywords, thumbnails and local playback. Filter by source, keyword, query, uploader or date. Follow next_offset until null to retrieve the full history."),
    "clipfeed_generate_recommendations": (args.GenerateRecommendations, _recommend,
        "Generate recommendations with request-specific source, context, current video, exclusions, limit and refresh filters. Recommendations must be enabled. Returns a task ID; poll clipfeed_get_task. Does not change saved filters."),
    "clipfeed_list_recommendation_history": (args.RecommendationHistory, lambda request, base: _list_catalog(request, base, history=True),
        "Browse every persisted generated recommendation, oldest first by default, with source, query, uploader, date and fallback filters. Follow next_offset until null. Generated results are not proof the user saw them; use activity for actual impressions."),
    "clipfeed_list_watch_later": (args.ListVideos, _list_catalog,
        "Browse all saved Watch later videos from the beginning, with source, query, uploader and saved-date filters. Follow next_offset until null."),
    "clipfeed_add_watch_later": (args.AddWatchLater, _add_watch_later,
        "Save up to 100 individual video links to Watch later, with optional titles and descriptions. Existing entries are updated without duplication. Set fetch_titles false to skip automatic missing-title lookup."),
    "clipfeed_list_watch_history": (args.WatchHistory, _list_watched,
        "Browse all watched videos from the beginning with source, title/uploader query, date and completed filters. Returns cumulative watched seconds and completion per video; date refers to the latest watch. Follow next_offset until null."),
    "clipfeed_recent_tags": (args.RecentTags, _recent_tags,
        "Get tags on videos watched in the past 30 days, ordered by recency. Watched seconds are cumulative totals for those videos, not a 30-day duration count."),
    "clipfeed_list_activity": (args.ListActivity, _list_activity,
        "Browse persisted searches, playback observations and recommendation impressions from the beginning. Filters include event type and ISO dates. Follow next_offset until null. Logging begins when Connector integration is enabled."),
    "clipfeed_get_task": (args.GetTask, _get_task,
        "Poll an asynchronous search or recommendation task. A completed task contains its result. Results expire after 30 minutes or a server restart. Preserve result video_markdown in the reply."),
}


def tool_definitions() -> list[dict]:
    return [{"name": name, "description": description, "parameters": schema.model_json_schema()}
            for name, (schema, _, description) in _TOOLS.items()]


def dispatch(name: str, arguments: dict, base_url: str) -> dict:
    """Validate the whole input before a tool can change any state."""
    if name not in _TOOLS:
        raise LookupError("Unknown Connector tool")
    schema, handler, _ = _TOOLS[name]
    request = schema.model_validate(arguments)
    if name in {"clipfeed_search_videos", "clipfeed_generate_recommendations"}:
        return _submit(name, handler, request, base_url)
    return handler(request, base_url)
