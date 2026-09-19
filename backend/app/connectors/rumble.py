"""Rumble connector."""
from yt_dlp.networking.impersonate import ImpersonateTarget

from .ytdlp import YtDlpConnector


class RumbleConnector(YtDlpConnector):
    id = "rumble"
    name = "Rumble"
    domains = ("rumble.com",)

    def ydl_opts(self, dest_dir, quality, hook):
        return {**super().ydl_opts(dest_dir, quality, hook),
                "impersonate": ImpersonateTarget.from_str("chrome")}
