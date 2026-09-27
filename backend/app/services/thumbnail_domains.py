"""Shared thumbnail CDN policy, independent of recommendation providers."""
import sqlite3
from urllib.parse import urlsplit


def configured_domains() -> tuple[str, ...]:
    from . import db
    try:
        return tuple(db.get_recommendation_settings().get("thumbnail_domains", ()))
    except sqlite3.OperationalError as exc:
        # Standalone discovery tools can run before the first DB migration.
        if "no such table: recommendation_settings" not in str(exc):
            raise
        return ()


def safe_cdn_url(value) -> str | None:
    """Allow configured image hosts for every website, with HTTPS only."""
    if (not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme == "https" and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443)
                and any(host == root or host.endswith("." + root) for root in configured_domains())):
            return value
    except (ValueError, UnicodeError):
        pass
    return None
