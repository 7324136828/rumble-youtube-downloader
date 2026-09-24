"""Validated public websites used for recommendation discovery."""
import ipaddress
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

DEFAULT_PROVIDERS = [
    {"id": "youtube", "name": "YouTube", "domain": "youtube.com", "enabled": True},
    {"id": "rumble", "name": "Rumble", "domain": "rumble.com", "enabled": True},
]
_BUILTINS = {"youtube.com": "youtube", "rumble.com": "rumble"}
_RESERVED_SUFFIXES = ("localhost", "local", "internal", "invalid", "test", "example", "home", "lan", "onion")


def default_providers() -> list[dict]:
    return [dict(provider) for provider in DEFAULT_PROVIDERS]


def normalize_domain(value: str) -> str:
    """Accept a public DNS name or homepage URL, never credentials/paths/ports."""
    value = value.strip()
    if not value or any(char.isspace() for char in value) or "\\" in value:
        raise ValueError("Use a public website domain, such as vimeo.com")
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                or parsed.port is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ValueError("Use a website domain without a path, port, or login details")
        domain = (parsed.hostname or "").lower().rstrip(".").encode("idna").decode("ascii")
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Use a public website domain without a path, port, or login details") from exc
    if domain.startswith("www."):
        domain = domain[4:]
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses cannot be recommendation websites")
    labels = domain.split(".")
    if (len(domain) > 253 or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or not re.fullmatch(r"(?:[a-z]{2,63}|xn--[a-z0-9-]+)", labels[-1])
            or any(domain == suffix or domain.endswith("." + suffix) for suffix in _RESERVED_SUFFIXES)
            or domain in ("example.com", "example.net", "example.org")):
        raise ValueError("Use a public website domain, such as vimeo.com")
    return domain


def normalize_search_url(value: str | None, domain: str) -> str | None:
    """Validate an HTTPS search template without resolving or contacting its host."""
    if value is None:
        return None
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Use an HTTPS search URL without control characters")
    value = value.strip()
    if not value:
        return None
    if (len(value) > 2048 or "\\" in value or "#" in value
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or any(ord(char) < 32 or ord(char) == 127 for char in unquote(value))
            or "\\" in unquote(value)):
        raise ValueError("Use an HTTPS search URL without fragments or control characters")
    if "{query}" not in value and value.endswith("="):
        value += "{query}"
    if value.count("{query}") != 1 or "{" in value.replace("{query}", "") or "}" in value.replace("{query}", ""):
        raise ValueError("The search URL must contain one {query} placeholder or end with =")
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
        normalize_domain(host)
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or "{query}" in parsed.netloc
                or not (host == domain or host.endswith("." + domain))
                or "{query}" not in parsed.path + parsed.query):
            raise ValueError("The search URL must use HTTPS on this website or one of its subdomains")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("The search URL must use HTTPS on this website or one of its subdomains") from exc
    # A domain joined to a root-relative template is commonly entered as
    # ``https://site.example//?search=...``. Treat repeated path separators at
    # the beginning as the intended root page instead of sending a surprising
    # double-slash request to the provider.
    path = re.sub(r"^/{2,}", "/", parsed.path or "/")
    normalized = urlunsplit(("https", host, path, parsed.query, ""))
    if len(normalized) > 2048:
        raise ValueError("The normalized search URL must be at most 2048 characters")
    return normalized


class RecommendationProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(default="", max_length=253)
    name: str = Field(min_length=1, max_length=60)
    domain: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    search_url: str | None = Field(default=None, max_length=2048)
    thumbnail_domains: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("A website name is required")
        return value

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value):
        return normalize_domain(value)

    @field_validator("thumbnail_domains")
    @classmethod
    def clean_thumbnail_domains(cls, value):
        domains = [normalize_domain(domain) for domain in value]
        if len(set(domains)) != len(domains):
            raise ValueError("Thumbnail CDN domains must be unique")
        return domains

    @model_validator(mode="after")
    def canonical_id(self):
        expected = _BUILTINS.get(self.domain, self.domain)
        if self.id and self.id != expected:
            raise ValueError("Website ID must match its domain")
        self.id = expected
        self.search_url = normalize_search_url(self.search_url, self.domain)
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if self.search_url is None:
            value.pop("search_url", None)
        if not self.thumbnail_domains:
            value.pop("thumbnail_domains", None)
        return value


def validate_provider_list(providers: list[RecommendationProvider]) -> list[RecommendationProvider]:
    if len(providers) > 12:
        raise ValueError("Configure at most 12 recommendation websites")
    domains = [provider.domain for provider in providers]
    if len(set(domains)) != len(domains):
        raise ValueError("Recommendation websites must have unique domains")
    if any(first.endswith("." + second) or second.endswith("." + first)
           for index, first in enumerate(domains) for second in domains[index + 1:]):
        raise ValueError("Recommendation websites cannot overlap; a domain already includes its subdomains")
    return providers
