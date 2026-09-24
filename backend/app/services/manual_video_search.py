"""Manual video search across enabled websites, independent of AI settings."""
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest

from ..schemas.search import SearchResponse, SearchResult, SearchWarning
from . import (custom_website_search, db, native_provider_search, recommendation_mix,
               recommendation_providers, recommendation_tools, search, video_title_lookup,
               connector_activity)

_LOG = logging.getLogger(__name__)
_BUILTINS = frozenset({"youtube", "rumble"})
SEARCH_DEADLINE = 45


def _record_search(query, source, count, session_id):
    try:
        connector_activity.record_search(query, source, count, session_id=session_id)
    except Exception:
        _LOG.warning("Could not record search activity for The Connector.")


class InvalidSearch(ValueError):
    """The requested query or website is not eligible for a manual search."""


def _video(raw, provider) -> SearchResult | None:
    if not isinstance(raw, dict):
        return None
    normalized = recommendation_tools.canonical_video_url(raw.get("source_url"), [provider])
    title = search._text(raw.get("title"))
    if not normalized or normalized[0] != provider["id"] or not title:
        return None
    # Never promote unverified results merely because the user explicitly
    # searched that website. Manual search displays them with their real label.
    builtin = provider["id"] in _BUILTINS
    verified = builtin or (raw.get("verified") is True and raw.get("verification") == "provider_search")
    thumbnail = (search._thumbnail(raw.get("thumbnail_url"), provider["id"]) if builtin else
                 video_title_lookup.safe_thumbnail_url(
                     raw.get("thumbnail_url"), normalized[1], provider))
    try:
        duration = search._duration(raw.get("duration"))
    except (OverflowError, ValueError):
        duration = None
    return SearchResult(id=normalized[2], connector=normalized[0], source_url=normalized[1],
                        title=title, description=native_provider_search._title(raw.get("description")),
                        uploader=search._text(raw.get("uploader")), duration=duration,
                        thumbnail_url=thumbnail, provider_name=provider["name"], verified=verified,
                        verification="provider_search" if verified else "custom_search",
                        origins=recommendation_mix.origins({**raw, "verified": verified}))


def search_videos(query: str, source: str = "all", limit: int = 12,
                  session_id: str | None = None, fetch_all: bool = False) -> SearchResponse:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
        raise InvalidSearch("Search must contain 1 to 200 characters.")
    if type(limit) is not int or not 1 <= limit <= 24:
        raise InvalidSearch("Search limit must be between 1 and 24.")
    if type(fetch_all) is not bool:
        raise InvalidSearch("Fetch all links must be true or false.")
    if session_id is not None and (not isinstance(session_id, str) or not 1 <= len(session_id) <= 64):
        raise InvalidSearch("Search session is invalid.")
    session_id = session_id or uuid.uuid4().hex
    query = query.strip()
    configured = recommendation_providers.configured_providers()
    available = {provider["id"]: provider for provider in configured}
    if source != "all" and source not in available:
        raise InvalidSearch("Select an enabled search website or all websites.")
    selected = configured if source == "all" else [available[source]]
    if not selected:
        _record_search(query, source, 0, session_id)
        return SearchResponse(query=query, source=source, results=[], warnings=[])
    deadline = time.monotonic() + SEARCH_DEADLINE

    def guard():
        if time.monotonic() >= deadline:
            raise search.SearchError("Video search timed out. Try searching a single website.")

    def run(provider):
        try:
            guard()
            # Ask for one extra candidate so ``has_more`` reflects a real next
            # result rather than merely a full current page.
            provider_limit = min(24, limit + 1)
            if provider["id"] in _BUILTINS:
                # Keep the original primitive restricted to YouTube/Rumble;
                # recommendation tools also rely on that trust boundary.
                response = search.search_videos(query, provider["id"], provider_limit)
                raw_results = [item.model_dump() for item in response.results]
                messages = [warning.message for warning in response.warnings]
                failed = False
            else:
                provider_limit = (custom_website_search.MAX_LINK_RESULTS if fetch_all
                                  and provider.get("search_url") else provider_limit)
                response = (recommendation_providers.search_website(
                    provider, query, provider_limit, guard, fetch_all=True) if fetch_all else
                    recommendation_providers.search_website(provider, query, provider_limit, guard))
                raw_results = response.get("results", [])
                messages = response.get("warnings", [])
                failed = response.get("status") == "unavailable" and not raw_results
            guard()
            if not isinstance(raw_results, list) or not isinstance(messages, list):
                raise search.SearchError(f"{provider['name']} returned an unreadable search response.")
            messages = [message.strip() for message in messages if isinstance(message, str) and message.strip()]
            result_limit = custom_website_search.MAX_LINK_RESULTS if fetch_all else 24
            results = [item for raw in raw_results[:result_limit] if (item := _video(raw, provider)) is not None]
            if raw_results and not results:
                failed = True
                messages = [*messages, f"{provider['name']} did not return usable individual video links."]
            if failed and not messages:
                messages = [f"{provider['name']} search is unavailable. Please try again shortly."]
            return {"results": results, "messages": messages, "failed": failed}
        except Exception as exc:
            message = (str(exc) if isinstance(exc, search.SearchError) else
                       f"{provider['name']} search is unavailable. Please try again shortly.")
            if provider["id"] not in _BUILTINS:
                _LOG.warning("%s search failed: %s", provider["id"], message)
            return {"results": [], "messages": [message], "failed": True}

    with ThreadPoolExecutor(max_workers=min(6, len(selected))) as executor:
        batches = list(executor.map(run, selected))
    warnings, seen_warnings = [], set()
    for provider, batch in zip(selected, batches):
        for message in batch["messages"]:
            if not isinstance(message, str) or not message.strip():
                continue
            key = (provider["id"], message)
            if key not in seen_warnings:
                seen_warnings.add(key)
                warnings.append(SearchWarning(source=provider["id"], message=message))
    if all(batch["failed"] for batch in batches):
        _record_search(query, source, 0, session_id)
        raise search.SearchError(" ".join(warning.message for warning in warnings))
    results, seen = [], set()
    for row in zip_longest(*(batch["results"] for batch in batches)):
        for item in row:
            if item is not None and item.id not in seen:
                seen.add(item.id)
                results.append(item)
    candidates = db.filter_presented_links(
        [item.model_dump() for item in results], session_id)
    result_limit = custom_website_search.MAX_LINK_RESULTS if fetch_all else limit
    has_more = not fetch_all and len(candidates) > limit and limit < 24
    presented = db.record_presented_links(candidates[:result_limit], session_id)
    if presented:
        # Search and Recommendations share the same exclusion history. A cached
        # recommendation must not reintroduce a link just shown in Search.
        from . import recommendations
        recommendations.clear_cache()
    _record_search(query, source, len(presented), session_id)
    return SearchResponse(query=query, source=source, results=presented,
                          warnings=warnings, has_more=has_more)
