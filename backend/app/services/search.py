"""Bounded, metadata-only searches of YouTube and Rumble's public results."""
import json
import logging
import math
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from itertools import zip_longest
from urllib.parse import quote, urlencode, urljoin, urlsplit

from curl_cffi import CurlOpt, requests

from ..schemas.search import SearchResponse, SearchResult, SearchSource, SearchWarning

SEARCH_TIMEOUT = 25
MAX_PAGE_BYTES = 2 * 1024 * 1024
_NAMES = {"youtube": "YouTube", "rumble": "Rumble"}
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_RUMBLE_PATH = re.compile(r"/(?P<id>v[a-z0-9]+)(?:-[\w.-]+)?\.html\Z", re.ASCII)
_LOG = logging.getLogger(__name__)


class SearchError(Exception):
    """A provider could not return trustworthy search results."""


def _text(value) -> str | None:
    if isinstance(value, str):
        return " ".join(value.split())[:500] or None
    return None


def _duration(value) -> float | None:
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) and value >= 0 else None
    return None


def _thumbnail(value, source: str) -> str | None:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return None
    try:
        parsed = urlsplit(value)
        roots = (("ytimg.com", "youtube.com") if source == "youtube" else
                 ("rumble.com", "rumble.cloud", "rmbl.ws", "1a-1791.com"))
        if (parsed.scheme in ("http", "https") and not parsed.username
                and not parsed.password and parsed.port in (None, 80, 443)
                and any(parsed.hostname == root or (parsed.hostname or "").endswith("." + root)
                        for root in roots)):
            return value
    except ValueError:
        pass
    return None


def _rumble_url(value: str) -> tuple[str, str] | None:
    if not value or any(c.isspace() for c in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(urljoin("https://rumble.com", value))
        match = _RUMBLE_PATH.fullmatch(parsed.path)
        if (match and parsed.scheme in ("http", "https")
                and parsed.hostname in ("rumble.com", "www.rumble.com")
                and not parsed.username and not parsed.password
                and parsed.port in (None, 80, 443)):
            return match["id"], "https://rumble.com" + parsed.path
    except ValueError:
        pass
    return None


def search_youtube(query: str, limit: int) -> list[SearchResult]:
    # A child process provides a hard deadline, including extractor retries or
    # hangs. It only requests flat search metadata and cannot download videos.
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
               "--flat-playlist", "--skip-download", "--dump-single-json",
               "--playlist-end", str(limit), "--socket-timeout", "8",
               "--retries", "0", "--extractor-retries", "0", "--no-cache-dir",
               "--quiet", "--no-warnings", "--", f"ytsearch{limit}:{query}"]
    try:
        process = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=SEARCH_TIMEOUT, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise SearchError("YouTube search timed out. Please try again.") from exc
    if process.returncode:
        raise SearchError("YouTube search is unavailable. Please try again shortly.")
    try:
        payload = json.loads(process.stdout)
        entries = payload["entries"]
        if not isinstance(entries, list):
            raise ValueError("Missing search entries")
    except (ValueError, KeyError, TypeError) as exc:
        raise SearchError("YouTube returned an unreadable search response.") from exc

    results, seen = [], set()
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        if (not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id)
                or video_id in seen or entry.get("_type") in ("playlist", "multi_video")):
            continue
        title = _text(entry.get("title"))
        if not title:
            continue
        seen.add(video_id)
        thumbnail = _thumbnail(entry.get("thumbnail"), "youtube")
        for image in entry.get("thumbnails") or []:
            if not thumbnail and isinstance(image, dict):
                thumbnail = _thumbnail(image.get("url"), "youtube")
        results.append(SearchResult(
            id=f"youtube:{video_id}", title=title, connector="youtube",
            source_url=f"https://www.youtube.com/watch?v={video_id}",
            uploader=_text(entry.get("uploader")) or _text(entry.get("channel")),
            duration=_duration(entry.get("duration")), thumbnail_url=thumbnail))
    return results


class _RumbleResultsParser(HTMLParser):
    """Read only public result cards, never sidebar/channel/navigation links."""
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.results: list[SearchResult] = []
        self.seen: set[str] = set()
        self.card = None
        self.stack = []
        self.empty = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = (attrs.get("class") or "").split()
        if tag == "article" and "video-item" in classes:
            self.card = {"title": [], "uploader": [], "url": None,
                         "thumbnail": None, "duration": None}
            self.stack = []
        if self.card is None:
            return
        field, ignored = self.stack[-1][1:] if self.stack else (None, False)
        ignored = ignored or tag in ("svg", "script", "style")
        if "video-item--title" in classes:
            field = "title"
        elif "video-item--by-a" in classes:
            field = "uploader"
        if tag == "a" and "video-item--a" in classes:
            self.card["url"] = _rumble_url(attrs.get("href") or "")
        if tag == "img" and "video-item--img" in classes:
            self.card["thumbnail"] = _thumbnail(
                attrs.get("src") or attrs.get("data-src"), "rumble")
        if "video-item--duration" in classes:
            value = attrs.get("data-value") or ""
            if re.fullmatch(r"\d{1,4}:\d{2}(?::\d{2})?", value):
                seconds = 0
                for part in value.split(":"):
                    seconds = seconds * 60 + int(part)
                self.card["duration"] = seconds
        if tag not in self._VOID_TAGS:
            self.stack.append((tag, field, ignored))

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if data.strip().lower() == "no videos found":
            self.empty = True
        if self.card is not None and self.stack:
            _, field, ignored = self.stack[-1]
            if field and not ignored:
                self.card[field].append(data)

    def handle_endtag(self, tag):
        if self.card is None:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag != "article":
            return
        card, self.card = self.card, None
        title = _text("".join(card["title"]))
        if not card["url"] or not title or len(self.results) >= self.limit:
            return
        video_id, source_url = card["url"]
        if video_id in self.seen:
            return
        self.seen.add(video_id)
        self.results.append(SearchResult(
            id=f"rumble:{video_id}", title=title, connector="rumble",
            source_url=source_url, uploader=_text("".join(card["uploader"])),
            duration=card["duration"], thumbnail_url=card["thumbnail"]))


def search_rumble(query: str, limit: int) -> list[SearchResult]:
    # Rumble's public search page includes all card metadata. No individual
    # video, embed, stream, account, or second-page requests are necessary.
    # Rumble redirects form-encoded spaces (+) to %20. Send the canonical query
    # directly so multi-word searches work without following arbitrary redirects.
    url = "https://rumble.com/search/video?" + urlencode({"q": query}, quote_via=quote)
    try:
        body = bytearray()
        # Streaming otherwise uses an inactivity timeout; enforce a total
        # transfer deadline as well, including slowly trickling responses.
        with requests.Session(curl_options={CurlOpt.TIMEOUT_MS: 15000}) as session:
            response = session.get(
                url,
                impersonate="chrome", timeout=15, allow_redirects=False, stream=True)
            try:
                if response.status_code != 200:
                    raise SearchError(f"Rumble search returned HTTP {response.status_code}. Please try again shortly.")
                for chunk in response.iter_content():
                    if len(body) + len(chunk) > MAX_PAGE_BYTES:
                        raise SearchError("Rumble returned an unexpectedly large search response.")
                    body.extend(chunk)
            finally:
                response.close()
    except SearchError:
        raise
    except requests.exceptions.RequestException as exc:
        raise SearchError("Rumble search could not be reached. Please try again.") from exc
    parser = _RumbleResultsParser(limit)
    parser.feed(body.decode("utf-8", errors="replace"))
    parser.close()
    if not parser.results and not parser.empty:
        raise SearchError("Rumble returned an unreadable search response. Please try again later.")
    return parser.results


def search_videos(query: str, source: SearchSource = "all", limit: int = 12) -> SearchResponse:
    providers = {"youtube": search_youtube, "rumble": search_rumble}
    selected = list(providers) if source == "all" else [source]
    warnings, batches = [], []
    with ThreadPoolExecutor(max_workers=len(selected)) as executor:
        futures = {name: executor.submit(providers[name], query, limit) for name in selected}
        for name, future in futures.items():
            try:
                batches.append(future.result())
            except Exception as exc:
                message = (str(exc) if isinstance(exc, SearchError) else
                           f"{_NAMES[name]} search is unavailable. Please try again shortly.")
                _LOG.warning("%s search failed: %s", name, message)
                warnings.append(SearchWarning(source=name, message=message))
    if len(warnings) == len(selected):
        raise SearchError(" ".join(warning.message for warning in warnings))
    results, seen = [], set()
    for row in zip_longest(*batches):
        for item in row:
            if item is not None and item.id not in seen:
                seen.add(item.id)
                results.append(item)
    return SearchResponse(query=query, source=source, results=results[:limit], warnings=warnings)
