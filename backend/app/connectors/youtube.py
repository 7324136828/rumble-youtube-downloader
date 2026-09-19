"""YouTube connector."""
from .ytdlp import YtDlpConnector


class YouTubeConnector(YtDlpConnector):
    id = "youtube"
    name = "YouTube"
    domains = ("youtube.com", "youtu.be", "music.youtube.com")

    def format_for(self, quality: str) -> str:
        heights = {"1080p": 1080, "720p": 720, "480p": 480}
        if quality in heights:
            h = heights[quality]
            return (f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio[ext=m4a]"
                    f"/bestvideo[height<={h}]+bestaudio"
                    f"/best[height<={h}]/best")
        return super().format_for(quality)
