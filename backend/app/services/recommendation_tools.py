"""Read-only search tools with platform allowlists and bounded work."""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from urllib.parse import parse_qs, urlsplit

from ..schemas.search import SearchResult
from . import custom_website_search, recommendation_providers, search

TOOLS = [
    {"type": "function", "function": {
        "name": "search_by_url", "description": "Look up an individual video on an enabled recommendation website. Custom-site metadata remains unverified unless the provider confirms it.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "search_by_key_words", "description": "Search up to six keywords/topics across enabled recommendation websites and interleave their results.",
        "parameters": {"type": "object", "properties": {
            "keywords": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
            "limit": {"type": "integer", "minimum": 1, "maximum": 24}},
            "required": ["keywords", "limit"], "additionalProperties": False}}},
]


class ToolError(Exception):
    pass


def _cache_details(response):
    """Only channels contributing videos determine cache status and age."""
    items = response.get("results", [])
    if not items:
        return False, None
    channels = [channel for channel in response.get("sources", [])
                if isinstance(channel, dict) and type(channel.get("count")) is int and channel["count"] > 0]
    if all(isinstance(item, dict) and isinstance(item.get("origins"), list) and item["origins"] for item in items):
        origins = {origin for item in items for origin in item["origins"] if isinstance(origin, str)}
        channels = [channel for channel in channels if channel.get("origin") in origins]
    contributing = channels or [response]
    stale = bool(contributing) and all(item.get("cache_status") == "stale" or item.get("status") == "cached"
                                      for item in contributing)
    ages = [item["cache_age_seconds"] for item in contributing
            if type(item.get("cache_age_seconds")) is int and item["cache_age_seconds"] >= 0]
    return stale, max(ages) if ages else None


def _merge_channels(channels):
    """Combine per-origin diagnostics without hiding earlier topic attempts."""
    groups = {}
    for channel in channels:
        if isinstance(channel, dict) and isinstance(channel.get("origin"), str):
            groups.setdefault(channel["origin"], []).append(channel)
    combined = []
    for origin, attempts in groups.items():
        bearing = [item for item in attempts if type(item.get("count")) is int and item["count"] > 0]
        stale = bool(bearing) and all(item.get("cache_status") == "stale" or item.get("status") == "cached" for item in bearing)
        status = ("cached" if stale else "ok" if bearing
                  else "unavailable" if any(item.get("status") == "unavailable" for item in attempts) else "empty")
        merged = {**attempts[-1], "origin": origin, "status": status,
                  "count": sum(item["count"] for item in bearing),
                  "warnings": list(dict.fromkeys(message for item in attempts for message in item.get("warnings", [])
                                                  if isinstance(message, str) and message.strip()))}
        merged.pop("cache_status", None)
        merged.pop("cache_age_seconds", None)
        if stale:
            merged["cache_status"] = "stale"
        ages = [item["cache_age_seconds"] for item in bearing
                if type(item.get("cache_age_seconds")) is int and item["cache_age_seconds"] >= 0]
        if ages:
            merged["cache_age_seconds"] = max(ages)
        checks = [check for item in attempts for check in item.get("checks", []) if isinstance(check, dict)]
        if checks:
            merged["checks"] = checks
        discoveries = list(dict.fromkeys(item.get("discovery") for item in attempts if item.get("discovery")))
        if discoveries:
            merged["discovery"] = discoveries[0] if len(discoveries) == 1 else "mixed"
        if len(attempts) > 1:
            merged["attempts"] = sum(item.get("attempts", 1) for item in attempts)
        combined.append(merged)
    return combined


def canonical_video_url(value: str, providers=None) -> tuple[str, str, str] | None:
    """Return (provider, canonical URL, provider-qualified ID)."""
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value) or "\\" in value:
        return None
    configured = recommendation_providers.configured_providers(providers)
    enabled_ids = {provider["id"] for provider in configured}
    if "://" not in value:
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
        elif host in ("rumble.com", "www.rumble.com") and "rumble" in enabled_ids:
            result = search._rumble_url(value)
            if result:
                return "rumble", result[1], "rumble:" + result[0]
        if "youtube" in enabled_ids and isinstance(video_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return "youtube", "https://www.youtube.com/watch?v=" + video_id, "youtube:" + video_id
        for provider in configured:
            if provider["id"] not in ("youtube", "rumble"):
                normalized = recommendation_providers.custom_video_url(value, provider)
                if normalized:
                    return normalized
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


def extract_video_metadata(url: str, guard=lambda: None, providers=None) -> dict:
    """Use yt-dlp's provider extractor for one already allowlisted video URL."""
    configured = recommendation_providers.configured_providers(providers)
    normalized = canonical_video_url(url, configured)
    if not normalized:
        raise ToolError("Use an individual video URL from an enabled recommendation website.")
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
        result = SearchResult(id=item_id, title=title, connector=source, source_url=canonical,
                            uploader=search._text(entry.get("uploader")) or search._text(entry.get("channel")),
                            duration=search._duration(entry.get("duration")),
                            thumbnail_url=(search._thumbnail(entry.get("thumbnail"), source)
                                           if source in ("youtube", "rumble") else None)).model_dump()
        return {**result, "verified": True}
    except (ValueError, TypeError) as exc:
        raise ToolError("Video metadata could not be loaded. Please try again.") from exc


def search_by_url(url: str, guard=lambda: None, providers=None) -> dict:
    configured = recommendation_providers.configured_providers(providers)
    normalized = canonical_video_url(url, configured)
    if not normalized:
        raise ToolError("Use an individual video URL from an enabled recommendation website.")
    _, canonical, _ = normalized
    try:
        return extract_video_metadata(canonical, guard, configured)
    except ToolError as exc:
        raise ToolError("Video metadata could not be loaded from the configured website.") from exc


def search_by_key_words(keywords: list[str], limit: int = 12, guard=lambda: None,
                        source: str = "all", providers=None, fetch_all: bool = False) -> dict:
    keywords = validate_keywords(keywords)
    if type(limit) is not int or not 1 <= limit <= 24:
        raise ToolError("Search limit must be between 1 and 24.")
    configured = recommendation_providers.configured_providers(providers)
    available = {provider["id"]: provider for provider in configured}
    if source != "all" and source not in available:
        raise ToolError("Select an enabled recommendation website or all websites.")
    selected = configured if source == "all" else [available[source]]
    if not selected:
        return {"results": [], "warnings": ["Enable at least one recommendation website in AI Picks settings."], "sources": []}
    builtin_sources = [provider["id"] for provider in selected if provider["id"] in ("youtube", "rumble")]
    builtin_source = "all" if len(builtin_sources) == 2 else (builtin_sources[0] if builtin_sources else None)

    def source_report(provider, response):
        items = response.get("results", [])
        warnings = list(dict.fromkeys(response.get("warnings", [])))
        stale, age = _cache_details(response)
        status = ("cached" if items and stale else "ok" if items
                  else "unavailable" if response.get("status") == "unavailable"
                  or (not response.get("status") and warnings) else "empty")
        result = {"id": provider["id"], "name": provider["name"], "status": status,
                  "count": len(items), "discovery": response.get("discovery", "provider_search"),
                  "warnings": warnings}
        if age is not None:
            result["cache_age_seconds"] = age
        if response.get("sources"):
            result["channels"] = _merge_channels(response["sources"])
        return result

    def run(keyword):
        guard()
        try:
            response = search.search_videos(keyword, builtin_source, limit)
        except search.SearchError as exc:
            guard()
            failed = {"results": [], "warnings": [str(exc)], "status": "unavailable"}
            return {**failed, "sources": [source_report(available[name], failed) for name in builtin_sources]}
        guard()
        items = [{**item.model_dump(), "verified": True, "origins": ["custom_search"]} for item in response.results]
        reports = [source_report(available[name], {
            "results": [item for item in items if item["connector"] == name],
            "warnings": [warning.message for warning in response.warnings if warning.source == name],
        }) for name in builtin_sources]
        return {"results": items, "warnings": [warning.message for warning in response.warnings], "sources": reports}

    def run_custom(provider):
        # Native searches expect a normal topic, not a web-engine-specific
        # expression of quoted OR clauses. Try one broader interest if the
        # first topic has no usable matches; do not retry successful searches.
        failures = []
        channel_attempts = []
        unavailable = False
        response = {"results": [], "warnings": [], "discovery": "provider_search"
                    if recommendation_providers.supports_native_search(provider["domain"])
                    else "custom_search"}
        for keyword in keywords[:2]:
            guard()
            try:
                provider_limit = (custom_website_search.MAX_LINK_RESULTS
                                  if fetch_all and provider.get("search_url") else limit)
                response = recommendation_providers.search_website(
                    provider, keyword, provider_limit, guard, fetch_all=fetch_all)
            except search.SearchError as exc:
                guard()
                failures.append(str(exc))
                unavailable = True
                continue
            guard()
            channel_attempts.extend(response.get("sources", []))
            if channel_attempts:
                response = {**response, "sources": _merge_channels(channel_attempts)}
            if response["results"]:
                return {**response, "sources": [source_report(provider, response)]}
            failures.extend(response.get("warnings", []))
            unavailable = unavailable or response.get("status") == "unavailable" or (
                not response.get("status") and bool(response.get("warnings")))
        result = {**response, "results": [], "warnings": list(dict.fromkeys(failures + response.get("warnings", [])))}
        if channel_attempts:
            result["sources"] = _merge_channels(channel_attempts)
        result["status"] = "unavailable" if unavailable else "empty"
        return {**result, "sources": [source_report(provider, result)]}

    # Fixed-size fan-out and at most two topic attempts per custom website.
    # Provider requests have deadlines; settings guards cancel stale work.
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(run, keyword) for keyword in keywords] if builtin_source else []
        futures.extend(executor.submit(run_custom, provider) for provider in selected
                       if provider["id"] not in ("youtube", "rumble"))
        batches = [future.result() for future in futures]
    guard()
    sources = {provider["id"]: [] for provider in selected}
    warnings, seen = [], set()
    for row in zip_longest(*(batch["results"] for batch in batches)):
        for item in row:
            if item and item["id"] not in seen:
                seen.add(item["id"])
                if item["connector"] in sources:
                    sources[item["connector"]].append(item)
    results = []
    if source == "all":
        for row in zip_longest(*sources.values()):
            results.extend(item for item in row if item)
    else:
        results = sources[source]
    for batch in batches:
        warnings.extend(batch["warnings"])
    reports = []
    for provider in selected:
        matching = [entry for batch in batches for entry in batch.get("sources", []) if entry["id"] == provider["id"]]
        messages = list(dict.fromkeys(message for entry in matching for message in entry["warnings"]))
        count = len(sources[provider["id"]])
        live = any(entry["status"] == "ok" for entry in matching)
        status = ("ok" if count and live else "cached" if count
                  else "unavailable" if any(entry["status"] == "unavailable" for entry in matching) else "empty")
        report = {"id": provider["id"], "name": provider["name"], "status": status, "count": count,
                  "discovery": matching[-1]["discovery"] if matching else "provider_search", "warnings": messages}
        ages = [entry["cache_age_seconds"] for entry in matching if entry["count"] and "cache_age_seconds" in entry]
        if ages:
            report["cache_age_seconds"] = max(ages)
        channels = [channel for entry in matching for channel in entry.get("channels", [])]
        if channels:
            report["channels"] = _merge_channels(channels)
        reports.append(report)
    return {"results": results[:limit], "all_results": results,
            "warnings": list(dict.fromkeys(warnings)), "sources": reports}


def dispatch(name: str, arguments: dict, guard=lambda: None, providers=None,
             source: str = "all", fetch_all: bool = False) -> dict:
    guard()
    if not isinstance(arguments, dict):
        raise ToolError("Tool arguments must be a JSON object.")
    if name == "search_by_url" and set(arguments) == {"url"}:
        normalized = canonical_video_url(arguments["url"], providers)
        if normalized and source != "all" and normalized[0] != source:
            raise ToolError("Use a video from the selected recommendation website.")
        result = search_by_url(arguments["url"], guard, providers=providers)
    elif name == "search_by_key_words" and set(arguments) in ({"keywords"}, {"keywords", "limit"}):
        result = search_by_key_words(arguments["keywords"], arguments.get("limit", 12), guard,
                                    source, providers=providers, fetch_all=fetch_all)
        # The full pool is for local synthesis, not duplicated in model messages.
        result.pop("all_results", None)
    else:
        raise ToolError("Unknown search tool or invalid arguments.")
    guard()
    return result
