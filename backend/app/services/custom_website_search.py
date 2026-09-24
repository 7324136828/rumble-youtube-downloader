"""Read a configured public search page without following page links or scripts.

DNS answers are checked and pinned for every HTTPS request. A short redirect chain
is allowed only while every hop remains on the configured provider's website.
Environment proxies, browser credentials, and arbitrary media requests are disabled.
Generic page discoveries remain unverified even when JSON-LD describes a video.
"""
import ipaddress
import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from html.parser import HTMLParser
from urllib.parse import quote, quote_plus, unquote, urljoin, urlsplit, urlunsplit

from curl_cffi import CurlOpt, requests

from ..schemas.recommendation_providers import normalize_domain, normalize_search_url
from . import native_provider_search, search

SEARCH_TIMEOUT = 12
DNS_TIMEOUT = 5
MAX_PAGE_BYTES = 1024 * 1024
MAX_REDIRECTS = 5
MAX_LINK_RESULTS = 200
METADATA_BUDGET = 10
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="search-dns")
_METADATA_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="search-metadata")
_OUTBOUND_LOG = logging.getLogger("uvicorn.error")


class CustomSearchError(search.SearchError):
    pass


class _MetadataBudgetExpired(Exception):
    pass


def _validated_page_url(provider, url, *, upgrade_same_site_http=False):
    if (not isinstance(url, str) or len(url) > 8192 or "#" in url or "\\" in url
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url)
            or "\\" in unquote(url)
            or any(ord(character) < 32 or ord(character) == 127 for character in unquote(url))):
        raise ValueError()
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
    normalize_domain(host)
    domain = provider["domain"]
    same_site = host == domain or host.endswith("." + domain)
    scheme = parsed.scheme
    port = parsed.port
    if (upgrade_same_site_http and scheme == "http" and port in (None, 80)
            and parsed.username is None and parsed.password is None and same_site):
        scheme = "https"
        port = None
    if (scheme != "https" or port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or not same_site):
        raise ValueError()
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, "")), host


def _public_ip(value):
    address = ipaddress.ip_address(value)
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_unspecified or address.is_loopback or address.is_link_local):
        return None
    if isinstance(address, ipaddress.IPv6Address):
        # Exclude transition formats which can route an embedded private IPv4.
        if (address not in ipaddress.IPv6Network("2000::/3")
                or address.ipv4_mapped or address.sixtofour or address.teredo):
            return None
    return address


def _resolve_public_address(host, name, guard):
    guard()
    future = _DNS_POOL.submit(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
    deadline = time.monotonic() + DNS_TIMEOUT
    try:
        while True:
            guard()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CustomSearchError(f"{name}'s configured search hostname could not be resolved in time.")
            try:
                answers = future.result(timeout=min(0.1, remaining))
                break
            except FutureTimeout:
                continue
    except OSError as exc:
        raise CustomSearchError(f"{name}'s configured search hostname could not be resolved.") from exc
    finally:
        future.cancel()
    guard()
    addresses = []
    for answer in answers:
        try:
            address = _public_ip(answer[4][0])
        except (ValueError, IndexError, TypeError):
            address = None
        if address is None:
            raise CustomSearchError(f"{name}'s configured search must resolve only to public addresses.")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise CustomSearchError(f"{name}'s configured search hostname could not be resolved.")
    # Prefer IPv4 when available, and pin exactly one already checked address.
    return min(addresses, key=lambda address: address.version)


def _search_page(provider, query, guard):
    try:
        template = normalize_search_url(provider.get("search_url"), provider["domain"])
    except ValueError as exc:
        raise CustomSearchError(f"{provider['name']}'s configured search URL is invalid.") from exc
    if not template:
        raise CustomSearchError(f"{provider['name']} has no configured search URL.")
    template_parts = urlsplit(template)
    encoded = quote(query, safe="") if "{query}" in template_parts.path else quote_plus(query, safe="")
    url = template.replace("{query}", encoded)
    return fetch_public_page(provider, url, guard)


def fetch_public_page(provider, url, guard=lambda: None, _redirects=0, _seen=None):
    """Fetch one same-provider HTTPS page using a checked, pinned public address."""
    guard()
    try:
        url, host = _validated_page_url(provider, url)
    except (ValueError, UnicodeError) as exc:
        raise CustomSearchError(f"{provider['name']} requires a public HTTPS page on its own website.") from exc
    seen = set() if _seen is None else _seen
    if url in seen or _redirects > MAX_REDIRECTS:
        raise CustomSearchError(f"{provider['name']}'s configured search redirected too many times.")
    seen.add(url)
    address = _resolve_public_address(host, provider["name"], guard)
    pinned = f"[{address}]" if address.version == 6 else str(address)
    options = {CurlOpt.RESOLVE: [f"{host}:443:{pinned}".encode()],
               CurlOpt.PROXY: "", CurlOpt.NOPROXY: "*", CurlOpt.NETRC: 0,
               CurlOpt.TIMEOUT_MS: SEARCH_TIMEOUT * 1000}
    guard()
    body = bytearray()
    redirect_url = None
    try:
        with requests.Session(trust_env=False, curl_options=options) as session:
            _OUTBOUND_LOG.info("External search request: GET %s (provider=%s)", url, provider["id"])
            response = session.get(url, timeout=SEARCH_TIMEOUT, allow_redirects=False,
                                   stream=True, verify=True, discard_cookies=True,
                                   headers={"Accept": "text/html, application/ld+json, application/json"})
            try:
                _OUTBOUND_LOG.info("External search response: GET %s -> HTTP %s (provider=%s)",
                                   url, response.status_code, provider["id"])
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not isinstance(location, str) or not location.strip():
                        raise CustomSearchError(
                            f"{provider['name']}'s configured search returned an invalid redirect.")
                    redirect_url = urljoin(url, location.strip())
                    # Some public sites still advertise an HTTP canonical URL.
                    # Never send the search over HTTP; retry its same-site target
                    # over HTTPS and let the normal validation/DNS pinning run.
                    try:
                        redirect_url, _ = _validated_page_url(
                            provider, redirect_url, upgrade_same_site_http=True)
                    except (ValueError, UnicodeError) as exc:
                        raise CustomSearchError(
                            f"{provider['name']} requires a public HTTPS page on its own website.") from exc
                    _OUTBOUND_LOG.info(
                        "External search redirect: GET %s -> %s (HTTP %s, provider=%s, hop=%s/%s)",
                        url, redirect_url, response.status_code, provider["id"],
                        _redirects + 1, MAX_REDIRECTS)
                elif response.status_code != 200:
                    raise CustomSearchError(f"{provider['name']}'s configured search is unavailable (HTTP {response.status_code}).")
                else:
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if content_type and content_type not in ("text/html", "application/xhtml+xml", "application/ld+json", "application/json"):
                        raise CustomSearchError(f"{provider['name']}'s configured search did not return a search page.")
                    for chunk in response.iter_content():
                        guard()
                        if len(body) + len(chunk) > MAX_PAGE_BYTES:
                            raise CustomSearchError(f"{provider['name']}'s configured search returned an oversized response.")
                        body.extend(chunk)
            finally:
                response.close()
    except requests.exceptions.RequestException as exc:
        raise CustomSearchError(f"{provider['name']}'s configured search could not be reached.") from exc
    guard()
    if redirect_url is not None:
        return fetch_public_page(provider, redirect_url, guard, _redirects + 1, seen)
    return url, body.decode("utf-8", errors="replace"), content_type


class _SearchPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.structured = [], []
        self.anchor = None
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json" and len(self.structured) < 40:
            self.script = []
        if tag == "a" and len(self.links) < 500:
            self.anchor = {"url": attrs.get("href"), "title": [],
                           "fallback": attrs.get("title") or attrs.get("aria-label"),
                           "thumbnail_url": None}
        if tag == "img" and self.anchor:
            if attrs.get("alt"):
                self.anchor["title"].append(attrs["alt"][:1000])
            thumbnail = next((attrs.get(name) for name in (
                "data-src", "data-original", "data-lazy-src", "data-thumb_url",
                "data-thumb", "data-poster", "src") if attrs.get(name)), None)
            if not thumbnail:
                srcset = attrs.get("data-srcset") or attrs.get("srcset")
                if srcset:
                    thumbnail = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
            self.anchor["thumbnail_url"] = thumbnail or self.anchor["thumbnail_url"]

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        elif self.anchor is not None:
            self.anchor["title"].append(data[:4000])

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.structured.append("".join(self.script))
            self.script = None
        if tag == "a" and self.anchor:
            self.links.append({"url": self.anchor["url"],
                               "name": "".join(self.anchor["title"]) or self.anchor["fallback"],
                               "thumbnail_url": self.anchor["thumbnail_url"]})
            self.anchor = None


class _ThumbnailDomainParser(HTMLParser):
    """Collect bounded image URL candidates without requesting their hosts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []
        self.structured = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and (attrs.get("type") or "").strip().lower() == "application/ld+json" and len(self.structured) < 40:
            self.script = []
        if len(self.values) >= 500:
            return
        value = None
        if tag == "meta" and (attrs.get("property") or attrs.get("name") or "").strip().lower() in (
                "og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src"):
            value = attrs.get("content")
        elif tag == "link" and "image_src" in (attrs.get("rel") or "").lower().split():
            value = attrs.get("href")
        elif tag == "video":
            value = attrs.get("poster")
        elif tag == "img":
            value = next((attrs.get(name) for name in (
                "data-src", "data-original", "data-lazy-src", "data-thumb_url",
                "data-thumb", "data-poster", "src") if attrs.get(name)), None)
            if not value:
                srcset = attrs.get("data-srcset") or attrs.get("srcset")
                if srcset:
                    value = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        if value:
            self.values.append(value)

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.structured.append("".join(self.script))
            self.script = None


def _image_value(item):
    value = item.get("thumbnailUrl") or item.get("thumbnail") or item.get("image") or item.get("contentUrl")
    if isinstance(value, list):
        value = next((entry for entry in value if isinstance(entry, (str, dict))), None)
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return value


def discover_thumbnail_domains(provider, query="video", guard=lambda: None):
    """Suggest external image hosts observed on one provider-owned page.

    Suggestions are never fetched or trusted here. The caller must require an
    explicit settings save before they become thumbnail allowlist entries.
    """
    if provider.get("search_url"):
        page_url, body, content_type = _search_page(provider, query, guard)
    else:
        page_url, body, content_type = fetch_public_page(
            provider, "https://" + provider["domain"] + "/", guard)
    parser = _ThumbnailDomainParser()
    parser.feed(body)
    parser.close()
    values = list(parser.values)
    documents = parser.structured + ([body] if content_type in ("application/json", "application/ld+json") else [])
    for document in documents:
        values.extend(_image_value(item) for item in _json_candidates(document))
    domains = []
    for value in values:
        if (not isinstance(value, str) or not value.strip() or len(value) > 2048
                or "\\" in value or any(character.isspace() or ord(character) < 32
                                          or ord(character) == 127 for character in value)):
            continue
        try:
            parsed = urlsplit(urljoin(page_url, value.strip()))
            host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
            host = normalize_domain(host)
            if (parsed.scheme != "https" or parsed.username or parsed.password
                    or parsed.port not in (None, 443)
                    or host == provider["domain"] or host.endswith("." + provider["domain"])):
                continue
        except (ValueError, UnicodeError):
            continue
        if host not in domains:
            domains.append(host)
        if len(domains) == 8:
            break
    return {"domains": domains, "page_url": page_url}


def _json_candidates(text):
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return []
    candidates, pending, inspected = [], [(document, 0)], 0
    while pending and inspected < 1000:
        node, depth = pending.pop()
        inspected += 1
        if depth > 20:
            continue
        if isinstance(node, dict):
            kind = node.get("@type")
            types = kind if isinstance(kind, list) else [kind]
            types = [value.rsplit("/", 1)[-1] for value in types if isinstance(value, str)]
            if "VideoObject" in types:
                candidates.append(node)
            elif "ListItem" in types:
                item = node.get("item")
                if isinstance(item, str):
                    candidates.append({**node, "url": item})
                elif isinstance(item, dict):
                    candidates.append({**node, **item})
                elif isinstance(node.get("url"), str):
                    candidates.append(node)
            pending.extend((value, depth + 1) for value in reversed(list(node.values())[:100]) if isinstance(value, (dict, list)))
        elif isinstance(node, list):
            pending.extend((value, depth + 1) for value in reversed(node[:500]))
    return candidates


def _candidate(raw, page_url, provider, fetch_all=False):
    from .recommendation_tools import canonical_video_url
    from .video_title_lookup import safe_thumbnail_url
    value = raw.get("url") or raw.get("mainEntityOfPage") or raw.get("@id")
    if isinstance(value, dict):
        value = value.get("@id") or value.get("url")
    if (not isinstance(value, str) or len(value) > 2048 or "\\" in value
            or any(character.isspace() or ord(character) < 32 for character in value)):
        return None
    url = urljoin(page_url, value)
    if url == page_url:
        return None
    normalized = canonical_video_url(url, [provider])
    title = native_provider_search._title(raw.get("name") or raw.get("headline"))
    if not normalized or (not title and not fetch_all):
        return None
    title = title or normalized[1]
    author = raw.get("author")
    if isinstance(author, dict):
        author = author.get("name")
    thumbnail = raw.get("thumbnail_url") or raw.get("thumbnailUrl") or raw.get("thumbnail")
    if isinstance(thumbnail, list):
        thumbnail = next((item for item in thumbnail if isinstance(item, str)), None)
    if isinstance(thumbnail, dict):
        thumbnail = thumbnail.get("url") or thumbnail.get("contentUrl")
    return {"id": normalized[2], "connector": normalized[0], "source_url": normalized[1],
            "title": title, "description": native_provider_search._title(raw.get("description")),
            "uploader": native_provider_search._title(author),
            "thumbnail_url": safe_thumbnail_url(thumbnail, page_url, provider), "duration": None,
            "verified": False, "verification": "custom_search", "origins": ["custom_search"]}


def _enrich_candidates(candidates, provider, guard):
    """Best-effort metadata reads for fetch-all links, preserving every fallback."""
    from . import video_title_lookup

    deadline = time.monotonic() + METADATA_BUDGET

    def enrich(candidate):
        def check():
            guard()
            if time.monotonic() >= deadline:
                raise _MetadataBudgetExpired()

        try:
            check()
            page_url, body, content_type = fetch_public_page(
                provider, candidate["source_url"], check)
            check()
            metadata = video_title_lookup.page_metadata(
                body, content_type, candidate["source_url"], provider, page_url)
        except (CustomSearchError, _MetadataBudgetExpired):
            return candidate
        return {**candidate,
                "title": metadata.get("title") or candidate["title"],
                "thumbnail_url": metadata.get("thumbnail_url") or candidate.get("thumbnail_url")}

    return list(_METADATA_POOL.map(enrich, candidates))


def search_custom(provider, query, limit=24, guard=lambda: None, fetch_all=False):
    maximum = MAX_LINK_RESULTS if fetch_all else 24
    if (not isinstance(query, str) or not query.strip() or len(query) > 200
            or type(limit) is not int or not 1 <= limit <= maximum or type(fetch_all) is not bool):
        raise CustomSearchError(f"Provide a search topic up to 200 characters and a limit between 1 and {maximum}.")
    page_url, body, content_type = _search_page(provider, query.strip(), guard)
    parser = _SearchPageParser()
    parser.feed(body)
    parser.close()
    raw_candidates = []
    if content_type in ("application/json", "application/ld+json"):
        raw_candidates.extend(_json_candidates(body))
    for document in parser.structured:
        raw_candidates.extend(_json_candidates(document))
    raw_candidates.extend(parser.links)
    from .title_quality import is_placeholder_title

    results, by_id = [], {}
    for raw in raw_candidates:
        guard()
        candidate = _candidate(raw, page_url, provider, fetch_all)
        if not candidate:
            continue
        existing = by_id.get(candidate["id"])
        if existing:
            for field in ("description", "uploader", "thumbnail_url", "duration"):
                if existing.get(field) is None and candidate.get(field) is not None:
                    existing[field] = candidate[field]
            if (is_placeholder_title(existing.get("title"))
                    and not is_placeholder_title(candidate.get("title"))):
                existing["title"] = candidate["title"]
        elif len(results) < limit:
            results.append(candidate)
            by_id[candidate["id"]] = candidate
    if fetch_all and results:
        results = _enrich_candidates(results, provider, guard)
        # Keep the complete set, but surface real video cards before generic
        # page links so available thumbnails are visible immediately.
        results.sort(key=lambda item: item.get("thumbnail_url") is None)
    return {"results": results, "warnings": [], "status": "ok" if results else "empty", "discovery": "custom_search"}
