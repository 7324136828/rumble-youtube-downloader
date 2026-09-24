"""Read-only cookie-file input and safe, actionable yt-dlp cookie diagnostics."""
import io
import re
from pathlib import Path

from .base import ConnectorError

MAX_COOKIE_FILE_BYTES = 10 * 1024 * 1024


def read_cookie_file(path: Path) -> io.StringIO:
    """Give yt-dlp an in-memory copy, so closing it cannot rewrite the export.

    Validate before handing data to yt-dlp: its permissive Netscape reader can
    print malformed lines (including credentials) to stderr.
    """
    try:
        if not path.is_file():
            raise ConnectorError("The configured cookie file does not exist or is not a file. "
                                 "Check the exported cookies.txt path in Download settings.")
        with path.open("rb") as source:
            data = source.read(MAX_COOKIE_FILE_BYTES + 1)
    except OSError:
        raise ConnectorError("Could not read the configured cookie file. Check its permissions "
                             "and use a file on the computer running ClipFeed's backend.") from None
    if len(data) > MAX_COOKIE_FILE_BYTES:
        raise ConnectorError("The configured cookie file is too large (maximum 10 MB). "
                             "Export only cookies for the video website.")
    invalid = "The configured cookie file must be a valid Netscape cookies.txt export, not JSON. Export it again."
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise ConnectorError(invalid) from None
    lines = text.splitlines()
    if (not lines or not re.match(r"^#(?: Netscape)? HTTP Cookie File", lines[0])
            or any(ord(char) < 32 and char not in "\t\r\n" or ord(char) == 127 for char in text)):
        raise ConnectorError(invalid)
    for line in lines[1:]:
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            raise ConnectorError(invalid)
        domain, subdomains, _path, secure, expires, _name, _value = fields
        if (not domain or any(char.isspace() for char in domain)
                or subdomains not in ("TRUE", "FALSE") or secure not in ("TRUE", "FALSE")
                or (subdomains == "TRUE") != domain.startswith(".")
                or (expires and not re.fullmatch(r"[0-9]{1,20}", expires))):
            raise ConnectorError(invalid)
    return io.StringIO("\n".join(lines) + "\n")


def _cookie_error_kind(message: str) -> str | None:
    text = message.lower()
    if "cookie" in text and ("could not copy" in text or "database is locked" in text
                             or "permission denied" in text):
        return "locked"
    if "dpapi" in text or "app-bound" in text or ("decrypt" in text and "cookie" in text):
        return "decrypt"
    if "could not find" in text and ("cookie" in text or "profile" in text):
        return "profile"
    return None


class CookieDiagnostics:
    """Avoid duplicate raw stderr errors; retain only a diagnostic category."""
    def __init__(self):
        self.kind = None

    def debug(self, message):
        pass

    def warning(self, message):
        self.error(message)

    def error(self, message):
        self.kind = _cookie_error_kind(str(message)) or self.kind

    def error_message(self, exc: Exception, browser: str | None, using_file: bool) -> str | None:
        # yt-dlp wraps CookieLoadError/DownloadError several times. Inspect the
        # chain without retaining or returning its potentially sensitive text.
        current, seen = exc, set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            self.error(str(current))
            current = current.__cause__ or current.__context__
        if using_file:
            return None
        name = {"edge": "Microsoft Edge", "chrome": "Google Chrome", "firefox": "Firefox"}.get(
            browser, (browser or "the selected browser").title())
        if self.kind == "locked" and browser:
            extra = (" Edge's Startup boost or background apps can keep it running after its windows close."
                     " yt-dlp calls this a 'Chrome' cookie database even when Edge is selected."
                     if browser == "edge" else "")
            return (f"Could not copy {name}'s cookie database; it may be locked or inaccessible. "
                    f"Fully exit {name}, including its background processes, then retry.{extra} "
                    "Alternatively, export Netscape cookies.txt and set Exported cookies file in Download settings "
                    "to avoid reading the browser database.")
        if self.kind == "decrypt" and browser:
            return (f"yt-dlp could not decrypt {name}'s cookies under the current OS account. "
                    "Export Netscape cookies.txt and set Exported cookies file in Download settings, "
                    "or choose another signed-in browser such as Firefox.")
        if self.kind == "profile" and browser:
            return (f"Could not find cookies for the selected {name} profile. Check the profile in Download "
                    "settings on the computer running ClipFeed's backend, or use an exported cookies.txt file.")
        return None
