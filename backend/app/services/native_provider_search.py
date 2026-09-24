"""Bounded anonymous searches using known providers' own public search clients.

Vimeo's public search-page JavaScript calls api.vimeo.com/search with its
anonymous viewer-bootstrap JWT. Bilibili TV's public search_v2 client uses
intl/gateway/web/v2/search_v2; Bilibili.com uses the same public video search
endpoint as yt-dlp's BiliBiliSearchIE. No browser cookies or user tokens are read.
Only the fixed endpoints below are requested, and redirects are never followed.
"""
import json
import logging
import re
import uuid
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from curl_cffi import CurlOpt, requests

from . import search

NATIVE_DOMAINS = frozenset({"vimeo.com", "bilibili.tv", "bilibili.com"})
NATIVE_TIMEOUT = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_OUTBOUND_LOG = logging.getLogger("uvicorn.error")
_THUMBNAIL_ROOTS = {
    "vimeo.com": ("vimeocdn.com",),
    "bilibili.tv": ("bstarstatic.com",),
    "bilibili.com": ("hdslb.com",),
}


class NativeSearchError(search.SearchError):
    """The native source failed; this must not be treated as an empty search."""


def supports_native_search(domain: str) -> bool:
    return domain in NATIVE_DOMAINS


def _get(session, url: str, name: str, guard, *, headers=None, cookies=None) -> bytes:
    guard()
    body = bytearray()
    try:
        _OUTBOUND_LOG.info("External search request: GET %s (provider=%s)", url, name)
        response = session.get(url, impersonate="chrome", timeout=NATIVE_TIMEOUT,
                               allow_redirects=False, stream=True, headers=headers, cookies=cookies)
        try:
            _OUTBOUND_LOG.info("External search response: GET %s -> HTTP %s (provider=%s)",
                               url, response.status_code, name)
            if response.status_code != 200:
                raise NativeSearchError(f"{name}'s own search is unavailable (HTTP {response.status_code}).")
            for chunk in response.iter_content():
                guard()
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise NativeSearchError(f"{name}'s own search returned an oversized response.")
                body.extend(chunk)
        finally:
            response.close()
    except requests.exceptions.RequestException as exc:
        raise NativeSearchError(f"{name}'s own search could not be reached.") from exc
    guard()
    return bytes(body)


def _json(session, url: str, name: str, guard, **kwargs) -> dict:
    try:
        payload = json.loads(_get(session, url, name, guard, **kwargs))
    except (ValueError, UnicodeError) as exc:
        raise NativeSearchError(f"{name}'s own search returned an unreadable response.") from exc
    if not isinstance(payload, dict):
        raise NativeSearchError(f"{name}'s own search returned an unreadable response.")
    return payload


class _ScriptDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = None
        self.scripts = {}

    def handle_starttag(self, tag, attrs):
        identifier = dict(attrs).get("id")
        if tag == "script" and identifier in ("viewer-bootstrap", "__NEXT_DATA__"):
            self.capture = identifier
            self.scripts[identifier] = []

    def handle_data(self, data):
        if self.capture:
            self.scripts[self.capture].append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = None


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _title(value) -> str | None:
    if not isinstance(value, str):
        return None
    parser = _TitleParser()
    parser.feed(value[:4000])
    parser.close()
    return search._text("".join(parser.parts))


def _duration(value) -> float | None:
    if isinstance(value, str) and re.fullmatch(r"\d{1,4}:\d{2}(?::\d{2})?", value):
        parts = [int(part) for part in value.split(":")]
        if any(part >= 60 for part in parts[1:]):
            return None
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return float(seconds)
    try:
        return search._duration(value)
    except (OverflowError, ValueError):
        return None


def _thumbnail(value, domain: str) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or any(char.isspace() for char in value):
        return None
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in ("http", "https") and not parsed.username and not parsed.password
                and parsed.port in (None, 80, 443)
                and any(parsed.hostname == root or (parsed.hostname or "").endswith("." + root)
                        for root in _THUMBNAIL_ROOTS[domain])):
            return value
    except ValueError:
        pass
    return None


def _item(provider, video_id, title, *, uploader=None, duration=None, thumbnail=None) -> dict | None:
    domain = provider["domain"]
    if (not isinstance(video_id, str) or not _title(title)
            or not re.fullmatch(r"BV[A-Za-z0-9]{10}|av\d{1,20}" if domain == "bilibili.com" else r"\d{1,20}", video_id)):
        return None
    path = ("/en/video/" if domain == "bilibili.tv" else "/video/" if domain == "bilibili.com" else "/")
    return {"id": provider["id"] + ":" + video_id, "connector": provider["id"],
            "source_url": "https://" + domain + path + video_id, "title": _title(title),
            "uploader": _title(uploader), "duration": _duration(duration),
            "thumbnail_url": _thumbnail(thumbnail, domain),
            "verified": True, "verification": "provider_search"}


def _vimeo(session, provider, query, limit, guard):
    page = _get(session, "https://vimeo.com/search?" + urlencode({"q": query}), provider["name"], guard)
    parser = _ScriptDataParser()
    parser.feed(page.decode("utf-8", errors="replace"))
    parser.close()
    try:
        if "viewer-bootstrap" in parser.scripts:
            bootstrap = json.loads("".join(parser.scripts["viewer-bootstrap"]))
        else:
            bootstrap = json.loads("".join(parser.scripts["__NEXT_DATA__"]))["props"]["pageProps"]["viewerBootstrap"]
        token = bootstrap["jwt"]
        if (bootstrap.get("user") is not None or not isinstance(token, str) or len(token) > 8192
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token)):
            raise ValueError("Expected anonymous viewer bootstrap")
    except (KeyError, ValueError, TypeError) as exc:
        raise NativeSearchError(f"{provider['name']}'s own search did not provide an anonymous search session.") from exc
    payload = _json(session, "https://api.vimeo.com/search?" + urlencode({
        "query": query, "filter_type": "clip", "page": 1, "per_page": limit,
        "fields": "clip.uri,clip.name,clip.link,clip.duration,clip.user.name,clip.pictures.sizes",
    }), provider["name"], guard, headers={
        "Authorization": "jwt " + token, "Accept": "application/vnd.vimeo.*+json;version=3.3",
        "Referer": "https://vimeo.com/",
    })
    entries = payload.get("data")
    # A successful schema, including an empty data list, is authoritative.
    if not isinstance(entries, list) or type(payload.get("total")) is not int:
        raise NativeSearchError(f"{provider['name']}'s own search returned an unreadable response.")
    results = []
    for entry in entries[:limit]:
        clip = entry.get("clip") if isinstance(entry, dict) else None
        if not isinstance(clip, dict):
            continue
        uri = clip.get("uri")
        match = re.fullmatch(r"/videos/(\d{1,20})", uri) if isinstance(uri, str) else None
        if not match:
            continue
        user, pictures = clip.get("user"), clip.get("pictures")
        images = pictures.get("sizes", []) if isinstance(pictures, dict) else []
        thumbnail = next((image.get("link") for image in reversed(images) if isinstance(image, dict)
                          and _thumbnail(image.get("link"), "vimeo.com")), None) if isinstance(images, list) else None
        item = _item(provider, match[1], clip.get("name"), uploader=user.get("name") if isinstance(user, dict) else None,
                     duration=clip.get("duration"), thumbnail=thumbnail)
        if item:
            results.append(item)
    if entries and not results:
        raise NativeSearchError(f"{provider['name']}'s own search returned no readable video records.")
    return results


def _bilibili_payload(payload, provider) -> dict:
    code = payload.get("code")
    if type(code) is not int or code != 0:
        # Do not echo upstream messages or token-bearing URLs into warnings.
        suffix = f" (code {code})" if type(code) is int else ""
        raise NativeSearchError(f"{provider['name']}'s own search is unavailable{suffix}.")
    if not isinstance(payload.get("data"), dict):
        raise NativeSearchError(f"{provider['name']}'s own search returned an unreadable response.")
    return payload["data"]


def _bilibili_tv(session, provider, query, limit, guard):
    # Parameters and module types are used by bilibili.tv's public search_v2
    # client. Series/creator/recommendation modules are not video search hits.
    payload = _json(session, "https://api.bilibili.tv/intl/gateway/web/v2/search_v2?" + urlencode({
        "keyword": query, "platform": "web", "s_locale": "en_US", "highlight": 1,
        "pn": 1, "ps": limit, "qid": "", "sort": 0, "duration_type": 0,
    }), provider["name"], guard, headers={"Referer": "https://www.bilibili.tv/"})
    data = _bilibili_payload(payload, provider)
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise NativeSearchError(f"{provider['name']}'s own search returned an unreadable response.")
    results, entries = [], []
    for module in modules[:20]:
        if not isinstance(module, dict) or module.get("type") != "ugc":
            continue
        if not isinstance(module.get("items"), list):
            raise NativeSearchError(f"{provider['name']}'s own search returned an unreadable response.")
        entries.extend(module["items"][:limit])
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        item = _item(provider, str(entry.get("aid") or ""), entry.get("title"),
                     uploader=author.get("nickname") if isinstance(author, dict) else None,
                     duration=entry.get("duration"), thumbnail=entry.get("cover"))
        if item:
            results.append(item)
    if entries and not results:
        raise NativeSearchError(f"{provider['name']}'s own search returned no readable video records.")
    return results


def _bilibili_com(session, provider, query, limit, guard):
    payload = _json(session, "https://api.bilibili.com/x/web-interface/search/type?" + urlencode({
        "search_type": "video", "keyword": query, "page": 1, "page_size": limit, "highlight": 0,
    }), provider["name"], guard, headers={"Referer": "https://www.bilibili.com/"},
        # An anonymous visitor ID, generated locally exactly as in yt-dlp;
        # never copy browser cookies or use a signed-in account.
        cookies={"buvid3": str(uuid.uuid4()) + "infoc"})
    data = _bilibili_payload(payload, provider)
    entries = data.get("result")
    if entries is None and data.get("numResults") == 0:
        entries = []
    if not isinstance(entries, list):
        raise NativeSearchError(f"{provider['name']}'s own search returned an unreadable response.")
    results = []
    for entry in entries[:limit]:
        if not isinstance(entry, dict) or entry.get("type") != "video":
            continue
        video_id = entry.get("bvid") or ("av" + str(entry["aid"]) if entry.get("aid") else "")
        item = _item(provider, video_id, entry.get("title"), uploader=entry.get("author"),
                     duration=entry.get("duration"), thumbnail=entry.get("pic"))
        if item:
            results.append(item)
    if entries and not results:
        raise NativeSearchError(f"{provider['name']}'s own search returned no readable video records.")
    return results


def search_native(provider: dict, query: str, limit: int, guard=lambda: None) -> dict:
    adapters = {"vimeo.com": _vimeo, "bilibili.tv": _bilibili_tv, "bilibili.com": _bilibili_com}
    if provider["domain"] not in adapters:
        raise NativeSearchError("This website has no native search adapter.")
    if not isinstance(query, str) or not query.strip() or len(query) > 2048 or type(limit) is not int or not 1 <= limit <= 24:
        raise NativeSearchError("Provide a search topic and a limit between 1 and 24.")
    with requests.Session(curl_options={CurlOpt.TIMEOUT_MS: NATIVE_TIMEOUT * 1000}) as session:
        results = adapters[provider["domain"]](session, provider, query.strip(), limit, guard)
    seen, unique = set(), []
    for item in results:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    return {"results": unique, "warnings": [], "status": "ok" if unique else "empty", "discovery": "provider_search"}
