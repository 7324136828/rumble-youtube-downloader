"""Recommendation websites with native or configured first-party search."""
import hashlib
import re
import sqlite3
from urllib.parse import parse_qs, urlencode, unquote, urlsplit, urlunsplit

from ..schemas.recommendation_providers import RecommendationProvider, default_providers
from . import custom_website_search, native_provider_search, search, search_cache
from .native_provider_search import supports_native_search

_NON_VIDEO_PATHS = {
    "about", "account", "accounts", "api", "blog", "category", "categories", "channel", "channels",
    "contact", "explore", "feed", "help", "home", "join", "legal", "login", "logout", "playlist",
    "playlists", "privacy", "profile", "profiles", "register", "search", "settings", "signin", "signup",
    "tag", "tags", "terms", "trending", "upload", "user", "users",
}


def clear_search_state() -> None:
    """Reset ephemeral discovery state; used by isolated tests."""
    search_cache.clear_cache()


def configured_providers(providers=None, *, enabled_only: bool = True) -> list[dict]:
    if providers is None:
        from . import db
        try:
            providers = db.get_recommendation_settings().get("providers", default_providers())
        except sqlite3.OperationalError as exc:
            # Standalone tool use before the application's first DB migration.
            if "no such table: recommendation_settings" not in str(exc):
                raise
            providers = default_providers()
    result = []
    for value in providers:
        provider = value if isinstance(value, RecommendationProvider) else RecommendationProvider.model_validate(value)
        if provider.enabled or not enabled_only:
            result.append(provider.model_dump())
    return result


def custom_video_url(value: str, provider: dict) -> tuple[str, str, str] | None:
    """Normalize one candidate without contacting its host or trusting redirects."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
        domain = provider["domain"]
        if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                or parsed.port not in (None, 80, 443)
                or not (host == domain or host.endswith("." + domain))):
            return None
        path = unquote(parsed.path).rstrip("/")
        if (not path or "\\" in path or "//" in path
                or any(char.isspace() or ord(char) < 32 for char in path)
                or any(part in (".", "..") for part in path.split("/"))):
            return None
        query = ""
        video_id = None
        if domain == "vimeo.com":
            match = re.fullmatch(r"/(?:video/|channels/[^/]+/|groups/[^/]+/videos/)?(\d+)(?:/([a-zA-Z0-9]+))?", path)
            if not match:
                return None
            video_id = match[1]
            path = "/" + video_id + ("/" + match[2] if match[2] else "")
            private_hash = parse_qs(parsed.query).get("h", [])
            if private_hash and re.fullmatch(r"[a-zA-Z0-9]+", private_hash[0]):
                query = urlencode({"h": private_hash[0]})
            host = domain
        elif domain == "bilibili.tv":
            match = re.fullmatch(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?video/(\d+)", path, re.I)
            if not match:
                return None
            video_id, path, host = match[1], "/en/video/" + match[1], domain
        elif domain == "bilibili.com":
            match = re.fullmatch(r"/video/((?:BV[A-Za-z0-9]+)|(?:av\d+))", path)
            if not match:
                return None
            video_id, path, host = match[1], "/video/" + match[1], domain
        elif domain == "instagram.com":
            match = re.fullmatch(r"/(reel|p|tv)/([A-Za-z0-9_-]+)", path)
            if not match:
                return None
            video_id, host = match[2], domain
        else:
            parts = path.lower().strip("/").split("/")
            if (parts[0] in _NON_VIDEO_PATHS or parts[0].startswith("@")
                    or re.search(r"\.(?:css|js|json|xml|rss|pdf|png|jpe?g|gif|svg|ico|webp|txt)$", path, re.I)):
                return None
            # Generic sites may identify videos in query strings (watch?v=...).
            # Remove tracking data but retain identity parameters and other
            # functional parameters instead of guessing a native site API.
            parameters = parse_qs(parsed.query, keep_blank_values=True)
            parameters = {key: values for key, values in parameters.items()
                          if not key.lower().startswith("utm_") and key.lower() not in ("fbclid", "gclid")}
            if len(parts) == 1 and parts[0] in ("watch", "video", "videos", "reels", "shorts") and not any(
                    parameters.get(key) for key in ("v", "id", "video", "video_id")):
                return None
            # Preserve escaped path delimiters. Decoding %3F/%23 into a raw
            # question mark/fragment would change the resource being linked.
            path = parsed.path.rstrip("/")
            query = urlencode(parameters, doseq=True)
            if host == "www." + domain:
                host = domain
        canonical = urlunsplit(("https", host, path, query, ""))
        identity = video_id or hashlib.sha256(canonical.encode()).hexdigest()[:24]
        return provider["id"], canonical, provider["id"] + ":" + identity
    except (ValueError, UnicodeError):
        return None


def _cached_attempt(mode, provider, query, limit, fetch, guard, maximum=24):
    if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= maximum:
        raise search.SearchError(f"Provide a search topic and a limit between 1 and {maximum}.")
    # Preserve case: video IDs and some providers' queries are case-sensitive.
    key = ("recommendation_provider", mode, provider["id"], provider["domain"], provider["name"],
           provider.get("search_url"), tuple(provider.get("thumbnail_domains", ())), query.strip())
    response = search_cache.cached_search(key, fetch, guard)
    return {**response, "results": response["results"][:limit]}


def search_website(provider: dict, query: str, limit: int, guard=lambda: None,
                   fetch_all: bool = False) -> dict:
    """Search only the provider's configured page or supported native API."""
    maximum = custom_website_search.MAX_LINK_RESULTS if fetch_all else 24
    if (not isinstance(query, str) or not query.strip() or len(query) > 200
            or type(limit) is not int or not 1 <= limit <= maximum or type(fetch_all) is not bool):
        raise search.SearchError(f"Provide a search topic up to 200 characters and a limit between 1 and {maximum}.")
    attempts = []
    if provider.get("search_url"):
        custom_limit = custom_website_search.MAX_LINK_RESULTS if fetch_all else 24
        def fetch_custom(current_guard):
            try:
                return custom_website_search.search_custom(
                    provider, query.strip(), custom_limit, current_guard, fetch_all=fetch_all)
            except custom_website_search.CustomSearchError as exc:
                return {"results": [], "warnings": [str(exc)], "status": "unavailable", "discovery": "custom_search"}
        cache_mode = "custom_template_all" if fetch_all else "custom_template"
        attempts.append(("custom_search", _cached_attempt(
            cache_mode, provider, query, custom_limit, fetch_custom, guard, custom_limit)))
    elif supports_native_search(provider["domain"]):
        def fetch_native(current_guard):
            try:
                return native_provider_search.search_native(provider, query.strip(), 24, current_guard)
            except native_provider_search.NativeSearchError as exc:
                return {"results": [], "warnings": [str(exc)], "status": "unavailable", "discovery": "provider_search"}
        attempts.append(("custom_search", _cached_attempt("native", provider, query, 24, fetch_native, guard)))
    else:
        attempts.append(("custom_search", {
            "results": [],
            "warnings": [f"{provider['name']} needs a configured search URL or supported website search."],
            "status": "unavailable", "discovery": "custom_search"}))
    from .recommendation_tools import canonical_video_url
    merged, order, buckets, warnings, checks, sources = {}, [], [], [], [], []
    for origin, response in attempts:
        bucket, eligible = [], set()
        for raw in response.get("results", []):
            if not isinstance(raw, dict):
                continue
            normalized = canonical_video_url(raw.get("source_url"), [provider])
            if not normalized:
                continue
            identity = normalized[2]
            eligible.add(identity)
            verified = raw.get("verified") is True and raw.get("verification") == "provider_search" and origin == "custom_search"
            item = {**raw, "id": identity, "connector": normalized[0], "source_url": normalized[1],
                    "origins": [origin], "verified": verified,
                    "verification": "provider_search" if verified else "custom_search"}
            if identity not in merged:
                merged[identity] = item
                bucket.append(identity)
            else:
                existing = merged[identity]
                origins = list(dict.fromkeys([*existing["origins"], origin]))
                if item["verified"] and not existing["verified"]:
                    existing = item
                for field in ("description", "uploader", "duration", "thumbnail_url"):
                    if existing.get(field) is None and item.get(field) is not None:
                        existing[field] = item[field]
                existing["origins"] = origins
                merged[identity] = existing
        buckets.append(bucket)
        source_warnings = [message for message in response.get("warnings", []) if isinstance(message, str) and message.strip()]
        warnings.extend(source_warnings)
        checks.extend({**check, "origin": origin} for check in response.get("checks", []) if isinstance(check, dict))
        source = {"origin": origin, "status": response.get("status", "empty"), "count": len(eligible),
                  "discovery": response.get("discovery"), "warnings": source_warnings}
        for field in ("cache_status", "cache_age_seconds"):
            if field in response:
                source[field] = response[field]
        sources.append(source)
    for position in range(max((len(bucket) for bucket in buckets), default=0)):
        order.extend(bucket[position] for bucket in buckets if position < len(bucket))
    results = [merged[identity] for identity in order[:limit]]
    return {"results": results, "warnings": list(dict.fromkeys(warnings)), "checks": checks, "sources": sources,
            "status": "ok" if results else "unavailable" if any(source["status"] == "unavailable" for source in sources) else "empty",
            "discovery": attempts[0][1].get("discovery", "custom_search")}
