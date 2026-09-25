"""Fetch one public page and extract a bounded set of ordinary web links."""
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from curl_cffi import CurlOpt, requests

from . import custom_website_search
from .title_quality import is_placeholder_title

MAX_REDIRECTS = 5
MAX_PAGE_BYTES = 100 * 1024 * 1024
MAX_RAW_LINKS = 1000
MAX_LINKS = 500
REQUEST_TIMEOUT = 12
_REDIRECTS = {301, 302, 303, 307, 308}
_NONVISIBLE_ELEMENTS = {"script", "style", "noscript", "template"}


class PageLinkImportError(ValueError):
    pass


class PageLinkUnavailable(Exception):
    pass


def _page_url(value):
    try:
        if (not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value
                or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
                or "\\" in unquote(value)):
            raise ValueError()
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
        if (parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80, 443) or parsed.fragment):
            raise ValueError()
        # Upgrade the submitted page itself; extracted links retain their declared scheme.
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit(("https", authority, parsed.path or "/", parsed.query, ""))
    except (UnicodeError, ValueError) as exc:
        raise PageLinkImportError("Enter a public HTTP or HTTPS page URL without credentials or a fragment.") from exc


def _request(url):
    parsed = urlsplit(url)
    try:
        address = custom_website_search._resolve_public_address(parsed.hostname, "The page", lambda: None)
    except custom_website_search.CustomSearchError as exc:
        raise PageLinkUnavailable("The page hostname could not be resolved to a public address.") from exc
    pinned = f"[{address}]" if address.version == 6 else str(address)
    options = {CurlOpt.RESOLVE: [f"{parsed.hostname}:443:{pinned}".encode()],
               CurlOpt.PROXY: "", CurlOpt.NOPROXY: "*", CurlOpt.NETRC: 0,
               CurlOpt.TIMEOUT_MS: REQUEST_TIMEOUT * 1000}
    body = bytearray()
    try:
        with requests.Session(trust_env=False, curl_options=options) as session:
            response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False, stream=True,
                                   verify=True, discard_cookies=True,
                                   headers={"Accept": "text/html,application/xhtml+xml"})
            try:
                if response.status_code in _REDIRECTS:
                    return response.status_code, response.headers.get("Location"), None
                if response.status_code != 200:
                    raise PageLinkUnavailable(f"The page could not be loaded (HTTP {response.status_code}).")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in ("text/html", "application/xhtml+xml"):
                    raise PageLinkImportError("The supplied URL did not return an HTML page.")
                for chunk in response.iter_content():
                    if len(body) + len(chunk) > MAX_PAGE_BYTES:
                        raise PageLinkImportError("The page is larger than 100 MiB.")
                    body.extend(chunk)
            finally:
                response.close()
    except requests.exceptions.RequestException as exc:
        raise PageLinkUnavailable("The page could not be reached.") from exc
    return 200, None, body.decode("utf-8", errors="replace")


def _fetch(value):
    current = _page_url(value)
    seen = set()
    for _ in range(MAX_REDIRECTS + 1):
        current = _page_url(current)
        if current in seen:
            raise PageLinkImportError("The page redirect contains a loop.")
        seen.add(current)
        status, location, body = _request(current)
        if status == 200:
            return current, body
        if not isinstance(location, str) or not location.strip() or len(location) > 2048:
            raise PageLinkImportError("The page returned an invalid redirect.")
        current = urljoin(current, location.strip())
    raise PageLinkImportError(f"The page redirected more than {MAX_REDIRECTS} times.")


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.anchor = None
        self.nonvisible_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _NONVISIBLE_ELEMENTS:
            self.nonvisible_depth += 1
        if self.nonvisible_depth:
            return
        if tag == "a" and len(self.links) < MAX_RAW_LINKS:
            values = dict(attrs)
            self.anchor = {"url": values.get("href"), "text": [],
                           "fallbacks": [values.get("aria-label"), values.get("title")], "image_titles": []}
        elif tag == "img" and self.anchor is not None:
            self.anchor["image_titles"].append(dict(attrs).get("alt"))

    def handle_data(self, data):
        if self.anchor is not None and self.nonvisible_depth == 0:
            self.anchor["text"].append(data[:2000])

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self.anchor is not None and self.nonvisible_depth == 0:
            self.links.append(self.anchor)
            self.anchor = None
        if tag in _NONVISIBLE_ELEMENTS and self.nonvisible_depth:
            self.nonvisible_depth -= 1


def _clean_title(*values):
    """Prefer a usable label over inline code, controls, or quality badges."""
    for value in values:
        title = " ".join((value or "").split())[:500]
        if not is_placeholder_title(title):
            return title
    return None


def _link(value, page_url):
    try:
        if (not isinstance(value, str) or not value.strip() or len(value) > 2048
                or "\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value)):
            return None
        parsed = urlsplit(urljoin(page_url, value.strip()))
        host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
        if (parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80, 443)):
            return None
        authority = f"[{host}]" if ":" in host else host
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, authority + port, parsed.path or "/", parsed.query, ""))
    except (UnicodeError, ValueError):
        return None


def extract_links(page_url):
    final_page, body = _fetch(page_url)
    parser = _Parser()
    parser.feed(body)
    parser.close()
    result, seen = [], {}
    for raw in parser.links:
        url = _link(raw["url"], final_page)
        if not url:
            continue
        anchor_text = " ".join("".join(raw["text"]).split())
        title = _clean_title(anchor_text, *raw["fallbacks"], *raw["image_titles"])
        if url in seen:
            # A thumbnail link may precede a separate descriptive title link.
            if not seen[url]["title"]:
                seen[url]["title"] = title
        elif len(result) < MAX_LINKS:
            item = {"url": url, "title": title}
            result.append(item)
            seen[url] = item
    if not result:
        raise PageLinkImportError("No HTTP or HTTPS links were found on this page.")
    return {"page_url": final_page, "links": result}
