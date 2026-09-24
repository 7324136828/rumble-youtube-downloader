"""Read-only title discovery for an explicitly requested configured video URL."""
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ..schemas.recommendation_providers import RecommendationProvider
from . import custom_website_search, native_provider_search, recommendation_tools
from .title_quality import is_placeholder_title

LOOKUP_TIMEOUT = 45
# A request already waiting for headers can take this long to return to guard().
REQUEST_RESERVE = custom_website_search.SEARCH_TIMEOUT
_CHALLENGE_TITLES = re.compile(
    r"^(?:just a moment|one moment(?: please)?|please wait|checking your browser|"
    r"checking if (?:the site connection|you are human)|verify (?:you are|that you are) human|"
    r"are you (?:a robot|human)|security (?:check|verification)|attention required|"
    r"access denied|forbidden|captcha|robot check|page not found|404(?: not found)?|"
    r"video (?:unavailable|not found)|this video is (?:unavailable|private)|"
    r"log ?in|sign ?in|sign ?up|authentication required|enable javascript|"
    r"cookies? (?:required|disabled))(?:[\s.!:|\-–—].*)?$", re.I)


class TitleLookupError(Exception):
    pass


def _title(value):
    title = native_provider_search._title(value)
    if not title:
        return None
    title = " ".join("".join(character for character in title
                            if ord(character) >= 32 and ord(character) != 127).split())[:500]
    if (is_placeholder_title(title) or _CHALLENGE_TITLES.fullmatch(title)
            or title.casefold().startswith(("http://", "https://", "www."))):
        return None
    return title


class _PageTitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {"og:title": [], "twitter:title": [], "og:image": [],
                     "og:image:url": [], "og:image:secure_url": [],
                     "twitter:image": [], "twitter:image:src": []}
        self.images = []
        self.titles, self.documents = [], []
        self.current_title = None
        self.current_script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            name = (attrs.get("property") or attrs.get("name") or "").strip().lower()
            if name in self.meta and len(self.meta[name]) < 10:
                self.meta[name].append(attrs.get("content"))
            itemprop = (attrs.get("itemprop") or "").strip().lower()
            if itemprop in ("thumbnail", "thumbnailurl", "image", "contenturl"):
                self.images.append(attrs.get("content"))
        elif tag == "link" and "image_src" in (attrs.get("rel") or "").lower().split():
            self.images.append(attrs.get("href"))
        elif tag == "video" and attrs.get("poster"):
            self.images.append(attrs.get("poster"))
        elif tag == "title" and len(self.titles) < 10:
            self.current_title = []
        elif tag == "script" and (attrs.get("type") or "").strip().lower() == "application/ld+json" and len(self.documents) < 40:
            self.current_script = []

    def handle_data(self, value):
        if self.current_script is not None:
            self.current_script.append(value)
        elif self.current_title is not None:
            self.current_title.append(value[:4000])

    def handle_endtag(self, tag):
        if tag == "script" and self.current_script is not None:
            self.documents.append("".join(self.current_script))
            self.current_script = None
        elif tag == "title" and self.current_title is not None:
            self.titles.append("".join(self.current_title))
            self.current_title = None


def _structured_titles(documents, canonical, provider):
    matched, unidentified = [], []
    for document in documents:
        for item in custom_website_search._json_candidates(document):
            types = item.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(isinstance(kind, str) and kind.rsplit("/", 1)[-1] == "VideoObject" for kind in types):
                continue
            title = _title(item.get("name"))
            if not title:
                continue
            reference = item.get("url") or item.get("mainEntityOfPage") or item.get("@id")
            if isinstance(reference, dict):
                reference = reference.get("@id") or reference.get("url")
            if reference is None:
                unidentified.append(title)
            elif isinstance(reference, str):
                try:
                    normalized = recommendation_tools.canonical_video_url(urljoin(canonical, reference), [provider])
                except ValueError:
                    continue
                if normalized and normalized[1] == canonical:
                    matched.append(title)
    # Multiple unidentified videos can be a related-video carousel, not this page.
    unique = list(dict.fromkeys(unidentified))
    return matched + (unique if len(unique) == 1 else [])


def _page_title(body, content_type, canonical, provider):
    return page_metadata(body, content_type, canonical, provider).get("title")


def safe_thumbnail_url(value, page_url, provider):
    if (not isinstance(value, str) or not value.strip() or len(value) > 2048
            or "\\" in value or any(character.isspace() or ord(character) < 32
                                      or ord(character) == 127 for character in value)):
        return None
    candidate = urljoin(page_url, value.strip())
    try:
        safe_url, _ = custom_website_search._validated_page_url(
            provider, candidate, upgrade_same_site_http=True)
        return safe_url
    except (ValueError, UnicodeError):
        pass
    # Native search implementations have their own fixed, audited image hosts.
    if native_provider_search.supports_native_search(provider["domain"]):
        thumbnail = native_provider_search._thumbnail(candidate, provider["domain"])
        if thumbnail and thumbnail.startswith("https://"):
            return thumbnail
    try:
        parsed = urlsplit(candidate)
        roots = provider.get("thumbnail_domains", ())
        hostname = (parsed.hostname or "").lower()
        if (parsed.scheme == "https" and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443)
                and any(hostname == root or hostname.endswith("." + root) for root in roots)):
            return candidate
    except (ValueError, UnicodeError):
        pass
    return None


def page_metadata(body, content_type, canonical, provider, page_url=None):
    """Read safe display metadata from one already-fetched video page."""
    parser = _PageTitleParser()
    parser.feed(body)
    parser.close()
    documents = parser.documents + ([body] if content_type in ("application/json", "application/ld+json") else [])
    resolved_page = page_url or canonical
    candidates = [*parser.meta["og:title"], *parser.meta["twitter:title"],
                  *_structured_titles(documents, resolved_page, provider), *parser.titles]
    for value in candidates:
        title = _title(value)
        if title and title.casefold() not in {provider["name"].casefold(), provider["domain"].casefold()}:
            break
    else:
        title = None
    thumbnails = [*parser.meta["og:image"], *parser.meta["og:image:url"],
                  *parser.meta["og:image:secure_url"], *parser.meta["twitter:image"],
                  *parser.meta["twitter:image:src"], *parser.images]
    thumbnail = next((safe for value in thumbnails
                      if (safe := safe_thumbnail_url(value, resolved_page, provider))), None)
    return {"title": title, "thumbnail_url": thumbnail}


def lookup_title(source_url: str, provider: dict, guard=lambda: None) -> str:
    """Return a title only; this never saves records or changes verification."""
    deadline = time.monotonic() + LOOKUP_TIMEOUT - REQUEST_RESERVE

    def check():
        guard()
        if time.monotonic() >= deadline:
            raise TitleLookupError("Title lookup timed out. Please try again.")

    check()
    try:
        # A user-requested saved-video lookup also works for disabled providers.
        provider = {**RecommendationProvider.model_validate(provider).model_dump(), "enabled": True}
    except (ValueError, TypeError) as exc:
        raise TitleLookupError("Choose a video from a configured website.") from exc
    normalized = recommendation_tools.canonical_video_url(source_url, [provider])
    if not normalized:
        raise TitleLookupError("Use an individual video URL from this configured website.")
    canonical = normalized[1]
    try:
        metadata = recommendation_tools.extract_video_metadata(canonical, check, providers=[provider])
    except recommendation_tools.ToolError:
        metadata = {}
    check()
    title = _title(metadata.get("title"))
    if title:
        return title
    if provider["id"] not in ("youtube", "rumble"):
        try:
            page_url, body, content_type = custom_website_search.fetch_public_page(provider, canonical, check)
            check()
            title = page_metadata(body, content_type, canonical, provider, page_url).get("title")
        except custom_website_search.CustomSearchError:
            title = None
        check()
        if title:
            return title
    raise TitleLookupError("The title could not be fetched automatically. Add a title below or retry.")
