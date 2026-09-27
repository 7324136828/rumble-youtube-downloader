"""URL normalization for videos explicitly supplied by a user."""
import hashlib
import ipaddress
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from ..schemas.recommendation_providers import default_providers


def public_host(value):
    host = value.lower().rstrip('.').encode('idna').decode('ascii')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split('.')
        if (len(host) > 253 or len(labels) < 2
                or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)
                or any(host == suffix or host.endswith('.' + suffix) for suffix in
                       ('localhost', 'local', 'internal', 'home', 'lan', 'invalid', 'test', 'example', 'onion'))):
            raise ValueError('Use a public video website.')
    else:
        if not address.is_global:
            raise ValueError('Use a public video website.')
    return host


def normalize_video_url(value, providers=()):
    """Keep known video identities, accepting other public websites without opt-in."""
    from .recommendation_tools import canonical_video_url
    if (not isinstance(value, str) or not value or len(value) > 2048
            or "\\" in value or "\\" in unquote(value)
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or any(ord(char) < 32 or ord(char) == 127 for char in unquote(value))):
        return None
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme not in ("http", "https") or parsed.username is not None
                or parsed.password is not None):
            return None
        host = public_host(parsed.hostname or "")
        domain = host.removeprefix('www.')
        configured = {item["id"]: {**item, "enabled": True} for item in [*default_providers(), *providers]}
        normalized = canonical_video_url(value, list(configured.values()))
        if normalized:
            return normalized
        # Native sites have known video routes; retain that validation even if
        # their recommendation provider is removed or disabled.
        if (domain in ("youtube.com", "youtu.be", "rumble.com")
                or domain.endswith((".youtube.com", ".rumble.com", ".youtu.be"))):
            return None
        default_port = 443 if parsed.scheme == "https" else 80
        authority = f'[{host}]' if ':' in host else host
        if parsed.port not in (None, default_port):
            authority += f':{parsed.port}'
        canonical = urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
        identity = hashlib.sha256(canonical.encode()).hexdigest()[:24]
        source = domain.replace(':', '-')
        return source, canonical, source + ":" + identity
    except (ValueError, UnicodeError):
        return None
