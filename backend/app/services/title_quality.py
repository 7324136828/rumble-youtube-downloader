"""Recognize labels that describe a player control or badge, not a video."""
import re

_GENERIC_TITLES = {"video", "play", "watch", "play video", "watch video", "view video", "click to play"}
_SCRIPT_CALL = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*\([^)]*\)\s*;?$", re.DOTALL)
_BADGE_TITLE = re.compile(r"(?:[1-9]\d{2,3}p(?:[1-9]\d{1,2})?|[248]k|uhd|fhd|hd|sd|hdr(?:10\+?)?|cc)+", re.I)


def is_placeholder_title(value):
    title = " ".join((value or "").split())
    # Adjacent spans often produce labels such as 1440pCC or 1080p / HD / CC.
    badges = re.sub(r"[\s|/,\-\u00b7\u2022_]+", "", title)
    return (not title or title.casefold() in _GENERIC_TITLES
            or title.casefold().startswith("javascript:")
            or bool(_SCRIPT_CALL.fullmatch(title) or _BADGE_TITLE.fullmatch(badges)))
