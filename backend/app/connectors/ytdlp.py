"""Shared yt-dlp connector implementation."""
import re
import threading
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

import yt_dlp

from .. import config
from ..services import media
from .base import Connector, ConnectorCancelled, ConnectorError, DownloadResult, VideoInfo
from .cookies import CookieDiagnostics, read_cookie_file

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _ProgressTracker:
    def __init__(self, on_progress: Callable[[float, str], None] | None,
                 cancel: threading.Event | None):
        self.on_progress = on_progress
        self.cancel = cancel
        self.files_done = 0
        self.last = 0.0

    def __call__(self, d: dict) -> None:
        if self.cancel and self.cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
        if d.get("postprocessor"):
            if self.files_done:
                processor = d["postprocessor"].lower()
                stage = "merging" if "merger" in processor else "processing"
                self._report(90, stage)
            return
        n = len(d.get("info_dict", {}).get("requested_formats") or [None])
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = d.get("downloaded_bytes", 0) / total if total else 0
            self._report(min(90, (self.files_done + pct) / n * 90), "downloading")
        elif d.get("status") == "finished":
            self.files_done += 1
            self._report(min(90, self.files_done / n * 90), "downloading")

    def _report(self, percent: float, stage: str) -> None:
        self.last = max(self.last, percent)
        if self.on_progress:
            self.on_progress(self.last, stage)


def _clean_message(exc: Exception) -> str:
    text = _ANSI_RE.sub("", str(exc))
    return re.sub(r"^(?:ERROR:\s*)+", "", text.strip()).strip()


class _SingleVideoYoutubeDL(yt_dlp.YoutubeDL):
    """Reject playlists before yt-dlp starts iterating/downloading entries."""
    def process_ie_result(self, ie_result, download=True, extra_info=None):
        if ie_result.get("_type") in ("playlist", "multi_video"):
            raise ConnectorError("Use a single video link instead of a playlist or channel.")
        return super().process_ie_result(ie_result, download, extra_info)


class YtDlpConnector(Connector):
    accepts_download_settings = True
    QUALITY_FORMATS = {
        "best": "bestvideo+bestaudio/best",
        "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    }

    def format_for(self, quality: str) -> str:
        return self.QUALITY_FORMATS.get(quality, self.QUALITY_FORMATS["best"])

    def ydl_opts(self, dest_dir: Path, quality: str, hook) -> dict:
        return {
            "format": self.format_for(quality),
            "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
            "restrictfilenames": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "retries": 3,
            "fragment_retries": 3,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "writethumbnail": True,
            "progress_hooks": [hook],
            "postprocessor_hooks": [hook],
            "js_runtimes": {"deno": {}, "node": {}},
        }

    def download(self, url: str, dest_dir: Path, quality: str = "best",
                 on_progress: Callable[[float, str], None] | None = None,
                 cancel: threading.Event | None = None,
                 download_settings: dict | None = None) -> DownloadResult:
        if cancel is not None and cancel.is_set():
            raise ConnectorCancelled("Download cancelled.")
        tracker = _ProgressTracker(on_progress, cancel)
        settings = download_settings or {}
        browser = settings.get("cookie_browser")
        profile = settings.get("cookie_browser_profile") or None
        cookie_path = settings.get("cookie_file") or config.YTDLP_COOKIE_FILE
        diagnostics = CookieDiagnostics()
        try:
            opts = self.ydl_opts(dest_dir, quality, tracker)
            opts["logger"] = diagnostics
            # The logger consumes warnings without printing them; keep them
            # enabled so skipped/undecryptable cookies still yield useful help.
            opts["no_warnings"] = False
            with ExitStack() as stack:
                # File mode must not touch the browser at all: yt-dlp otherwise
                # tries browser extraction first, defeating the lock workaround.
                if cookie_path:
                    opts["cookiefile"] = stack.enter_context(read_cookie_file(Path(cookie_path)))
                elif browser:
                    opts["cookiesfrombrowser"] = (browser, profile, None, None)
                ydl = stack.enter_context(_SingleVideoYoutubeDL(opts))
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise ConnectorError("The source did not return a downloadable video.")
                path = self._final_path(ydl, info, dest_dir)
        except yt_dlp.utils.DownloadCancelled as exc:
            raise ConnectorCancelled(str(exc)) from exc
        except ConnectorError:
            raise
        except Exception as exc:
            message = _clean_message(exc)
            lowered = message.lower()
            cookie_message = diagnostics.error_message(exc, browser, bool(cookie_path))
            if cookie_message:
                message = cookie_message
            elif "sign in to confirm" in lowered and "not a bot" in lowered:
                if cookie_path:
                    message = ("YouTube still requires verification with the exported cookies. Export fresh "
                               "YouTube cookies from a signed-in session, update the cookie file, then retry.")
                elif browser:
                    message = ("YouTube still requires verification. Confirm the selected browser is signed in "
                               "to YouTube, or set a fresh exported cookies.txt file in Download settings, then retry.")
                else:
                    message = ("YouTube requires browser verification. Open Download settings, choose the "
                               "browser where you are signed in to YouTube or an exported cookies.txt file, "
                               "save, then retry this download.")
            elif (browser or cookie_path) and (
                    "cookie" in lowered and any(word in lowered for word in ("failed", "error", "could not", "unable"))):
                message = (("Could not load the exported cookie file. Export a fresh Netscape cookies.txt file "
                            "and check its path in Download settings.") if cookie_path else
                           ("Could not load the selected browser cookies. Check the browser/profile in Download "
                            "settings, fully exit that browser, or use an exported cookies.txt file."))
            raise ConnectorError(message) from exc

        thumbnail = next(
            (dest_dir / f"{info.get('id')}{ext}"
             for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif")
             if (dest_dir / f"{info.get('id')}{ext}").is_file()),
            None)
        return DownloadResult(path=path, info=self._video_info(info, url),
                              thumbnail=thumbnail)

    def _final_path(self, ydl, info: dict, dest_dir: Path) -> Path:
        paths = [info.get("filepath")]
        paths.extend(item.get("filepath") for item in info.get("requested_downloads") or [])
        paths.append(ydl.prepare_filename(info))
        # The dedicated job directory also catches extensions changed by merging.
        paths.extend(dest_dir.glob("*"))
        for value in paths:
            if not value:
                continue
            path = Path(value).resolve()
            if (path.is_relative_to(dest_dir.resolve()) and path.is_file()
                    and path.suffix.lower() in media.VIDEO_EXTS):
                return path
        raise ConnectorError("Download produced no video file")

    def _video_info(self, info: dict, url: str) -> VideoInfo:
        return VideoInfo(
            title=info.get("title") or info.get("id") or url,
            source_id=info.get("id"),
            uploader=info.get("uploader") or info.get("channel"),
            duration=info.get("duration"),
            width=info.get("width"),
            height=info.get("height"),
            description=info.get("description"),
            thumbnail_url=info.get("thumbnail"),
            webpage_url=info.get("webpage_url"),
        )
