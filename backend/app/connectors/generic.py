"""Fallback connector for any site yt-dlp supports."""
from .base import http_url_host

from .ytdlp import YtDlpConnector


class GenericConnector(YtDlpConnector):
    id = "generic"
    name = "Other (yt-dlp)"
    domains = ()

    def matches(self, url: str) -> bool:
        return http_url_host(url) is not None
