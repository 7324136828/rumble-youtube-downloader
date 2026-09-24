"""Resolve configured video redirects without following an unchecked location."""
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from curl_cffi import CurlOpt, requests

from . import custom_website_search, recommendation_providers, recommendation_tools

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
REQUEST_TIMEOUT = 8
BATCH_TIMEOUT = 20
MAX_WORKERS = 8


class RedirectResolutionError(ValueError):
    pass


class RedirectUnavailable(Exception):
    pass


def _safe_page_url(value, providers):
    try:
        if (not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value
                or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
                or "\\" in unquote(value)
                or any(ord(character) < 32 or ord(character) == 127 for character in unquote(value))):
            raise ValueError()
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
        provider = next((item for item in providers if host == item["domain"] or host.endswith("." + item["domain"])), None)
        if (provider is None or parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment):
            raise ValueError()
        return urlunsplit(("https", host, parsed.path or "/", parsed.query, "")), provider
    except (UnicodeError, ValueError) as exc:
        raise RedirectResolutionError("A video redirect must stay on a configured public HTTPS website.") from exc


def _probe(url, provider, guard):
    parsed = urlsplit(url)
    try:
        address = custom_website_search._resolve_public_address(parsed.hostname, provider["name"], guard)
    except custom_website_search.CustomSearchError as exc:
        raise RedirectUnavailable("The video redirect host could not be checked.") from exc
    pinned = f"[{address}]" if address.version == 6 else str(address)
    options = {CurlOpt.RESOLVE: [f"{parsed.hostname}:443:{pinned}".encode()],
               CurlOpt.PROXY: "", CurlOpt.NOPROXY: "*", CurlOpt.NETRC: 0,
               CurlOpt.TIMEOUT_MS: REQUEST_TIMEOUT * 1000}
    guard()
    try:
        with requests.Session(trust_env=False, curl_options=options) as session:
            response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False, stream=True,
                                   verify=True, discard_cookies=True,
                                   headers={"Accept": "text/html,application/xhtml+xml", "Range": "bytes=0-0"})
            try:
                return response.status_code, response.headers.get("Location")
            finally:
                response.close()
    except requests.exceptions.RequestException as exc:
        raise RedirectUnavailable("The video redirect could not be checked.") from exc


def resolve_video_url(value, providers, guard=lambda: None):
    """Return a canonical final video URL, or the canonical input if probing is unavailable."""
    configured = recommendation_providers.configured_providers(providers, enabled_only=False)
    normalized = recommendation_tools.canonical_video_url(value, configured)
    if normalized is None:
        raise RedirectResolutionError("Every saved URL must be an individual video on a configured website.")
    original = normalized[1]
    current = original
    seen = set()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                current, provider = _safe_page_url(current, configured)
            except RedirectResolutionError:
                # Provider aliases such as youtu.be are valid video targets even
                # though their host is not the provider's configured base domain.
                alias = recommendation_tools.canonical_video_url(current, configured)
                if alias is None:
                    raise
                current, provider = _safe_page_url(alias[1], configured)
            if current in seen:
                raise RedirectResolutionError("The video redirect contains a loop.")
            seen.add(current)
            status, location = _probe(current, provider, guard)
            guard()
            if status not in REDIRECT_STATUSES:
                final = recommendation_tools.canonical_video_url(current, configured)
                if final is None:
                    raise RedirectResolutionError("The redirect did not end at an individual configured video.")
                return final[1]
            if not isinstance(location, str) or not location.strip() or len(location) > 2048:
                raise RedirectResolutionError("The video website returned an invalid redirect.")
            current = urljoin(current, location.strip())
            target = urlsplit(current)
            if (target.scheme != "https" or target.username is not None or target.password is not None
                    or target.port not in (None, 443) or target.fragment):
                raise RedirectResolutionError("A video redirect must stay on configured public HTTPS websites.")
        raise RedirectResolutionError(f"The video redirected more than {MAX_REDIRECTS} times.")
    except RedirectUnavailable:
        return original


def resolve_import(videos, providers):
    """Resolve a bounded import before its single database transaction."""
    if not videos:
        return videos
    deadline = time.monotonic() + BATCH_TIMEOUT

    def guard():
        if time.monotonic() >= deadline:
            raise RedirectUnavailable("Redirect checking timed out.")

    def resolve(video):
        try:
            return {**video, "source_url": resolve_video_url(video["source_url"], providers, guard)}
        except RedirectUnavailable:
            normalized = recommendation_tools.canonical_video_url(video["source_url"], providers)
            if normalized is None:
                raise RedirectResolutionError("Every saved URL must be an individual video on a configured website.")
            return {**video, "source_url": normalized[1]}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="watch-later-redirect") as executor:
        return list(executor.map(resolve, videos))
