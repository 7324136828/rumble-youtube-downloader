"""Read-only search tools with platform allowlists and bounded work."""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from urllib.parse import parse_qs, urlsplit

from ..schemas.search import SearchResult
from . import search

TOOLS = [
    {"type": "function", "function": {
        "name": "search_by_url", "description": "Read metadata of one YouTube or Rumble video without downloading it.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "search_by_key_words", "description": "Search up to six keywords/topics and alternate YouTube and Rumble results.",
        "parameters": {"type": "object", "properties": {
            "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
            "limit": {"type": "integer", "minimum": 1, "maximum": 24}},
            "required": ["keywords", "limit"], "additionalProperties": False}}},
]


class ToolError(Exception):
    pass


def canonical_video_url(value: str) -> tuple[str, str, str] | None:
    """Return (provider, canonical URL, provider-qualified ID)."""
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value) or "\\" in value:
        return None
    if re.match(r"^(?:(?:www\.|m\.|music\.)?youtube\.com|(?:www\.)?youtu\.be|(?:www\.)?rumble\.com)/", value, re.I):
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return None
        host = (parsed.hostname or "").lower()
        video_id = None
        if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            if parsed.path == "/watch":
                ids = parse_qs(parsed.query).get("v", [])
                video_id = ids[0] if len(ids) == 1 else None
            elif re.fullmatch(r"/(shorts|live|embed)/[A-Za-z0-9_-]{11}/?", parsed.path):
                video_id = parsed.path.split("/")[2]
        elif host in ("youtu.be", "www.youtu.be"):
            video_id = parsed.path.strip("/")
        elif host in ("rumble.com", "www.rumble.com"):
            result = search._rumble_url(value)
            if result:
                return "rumble", result[1], "rumble:" + result[0]
        if isinstance(video_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return "youtube", "https://www.youtube.com/watch?v=" + video_id, "youtube:" + video_id
    except ValueError:
        pass
    return None


def validate_keywords(value) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 6:
        raise ToolError("Provide one to six keywords or short topics.")
    result = []
    for keyword in value:
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 100:
            raise ToolError("Each keyword must contain 1 to 100 characters.")
        cleaned = " ".join(keyword.split())
        if cleaned.casefold() not in {item.casefold() for item in result}:
            result.append(cleaned)
    return result


def search_by_url(url: str, guard=lambda: None) -> dict:
    normalized = canonical_video_url(url)
    if not normalized:
        raise ToolError("Use an individual YouTube or Rumble video URL.")
    source, canonical, item_id = normalized
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
               "--skip-download", "--no-playlist", "--no-cache-dir", "--socket-timeout", "8",
               "--ignore-no-formats-error", "--js-runtimes", "node", "--js-runtimes", "deno",
               "--retries", "0", "--extractor-retries", "0", "--quiet", "--no-warnings",
               "--print", "%(.{id,title,uploader,channel,duration,thumbnail,_type})j", "--", canonical]
    if source == "rumble":
        command[3:3] = ["--impersonate", "chrome"]
    guard()
    try:
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=search.SEARCH_TIMEOUT, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError("Video metadata could not be loaded. Please try again.") from exc
    guard()
    try:
        if process.returncode or len(process.stdout) > 2 * 1024 * 1024:
            raise ValueError("Metadata unavailable")
        entry = json.loads(process.stdout)
        if not isinstance(entry, dict) or entry.get("_type") in ("playlist", "multi_video"):
            raise ValueError("Expected individual video")
        title = search._text(entry.get("title"))
        if not title or (source == "youtube" and item_id != "youtube:" + str(entry.get("id"))):
            raise ValueError("Missing metadata")
        return SearchResult(id=item_id, title=title, connector=source, source_url=canonical,
                            uploader=search._text(entry.get("uploader")) or search._text(entry.get("channel")),
                            duration=search._duration(entry.get("duration")),
                            thumbnail_url=search._thumbnail(entry.get("thumbnail"), source)).model_dump()
    except (ValueError, TypeError) as exc:
        raise ToolError("Video metadata could not be loaded. Please try again.") from exc


def search_by_key_words(keywords: list[str], limit: int = 12, guard=lambda: None) -> dict:
    keywords = validate_keywords(keywords)
    if type(limit) is not int or not 1 <= limit <= 24:
        raise ToolError("Search limit must be between 1 and 24.")

    def run(keyword):
        guard()
        try:
            response = search.search_videos(keyword, "all", limit)
        except search.SearchError as exc:
            guard()
            return {"results": [], "warnings": [str(exc)]}
        guard()
        return {"results": [item.model_dump() for item in response.results],
                "warnings": [warning.message for warning in response.warnings]}

    # Fixed-size fan-out: independent topics finish within two search deadlines.
    with ThreadPoolExecutor(max_workers=3) as executor:
        batches = list(executor.map(run, keywords))
    guard()
    sources = {"youtube": [], "rumble": []}
    warnings, seen = [], set()
    for row in zip_longest(*(batch["results"] for batch in batches)):
        for item in row:
            if item and item["id"] not in seen:
                seen.add(item["id"])
                sources[item["connector"]].append(item)
    results = []
    for row in zip_longest(sources["youtube"], sources["rumble"]):
        results.extend(item for item in row if item)
    for batch in batches:
        warnings.extend(batch["warnings"])
    return {"results": results[:limit], "warnings": list(dict.fromkeys(warnings))}


def dispatch(name: str, arguments: dict, guard=lambda: None) -> dict:
    guard()
    if not isinstance(arguments, dict):
        raise ToolError("Tool arguments must be a JSON object.")
    if name == "search_by_url" and set(arguments) == {"url"}:
        result = search_by_url(arguments["url"], guard)
    elif name == "search_by_key_words" and set(arguments) in ({"keywords"}, {"keywords", "limit"}):
        result = search_by_key_words(arguments["keywords"], arguments.get("limit", 12), guard)
    else:
        raise ToolError("Unknown search tool or invalid arguments.")
    guard()
    return result
