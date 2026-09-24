"""Connector contract for platform-specific video downloads."""
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


def http_url_host(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        # Accessing port also validates malformed/out-of-range port numbers.
        _ = parsed.port
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or any(character.isspace() for character in url)):
            return None
        return parsed.hostname.lower().rstrip(".")
    except (ValueError, TypeError):
        return None


@dataclass
class VideoInfo:
    title: str
    source_id: str | None = None
    uploader: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    webpage_url: str | None = None


@dataclass
class DownloadResult:
    path: Path
    info: VideoInfo
    thumbnail: Path | None = None


class ConnectorError(Exception):
    pass


class ConnectorCancelled(ConnectorError):
    pass


class Connector(ABC):
    id: str
    name: str
    domains: tuple[str, ...] = ()
    accepts_download_settings = False

    def matches(self, url: str) -> bool:
        host = http_url_host(url)
        if not host:
            return False
        return any(host == domain or host.endswith("." + domain)
                   for domain in self.domains)

    @abstractmethod
    def download(self, url: str, dest_dir: Path, quality: str = "best",
                 on_progress: Callable[[float, str], None] | None = None,
                 cancel: threading.Event | None = None,
                 download_settings: dict | None = None) -> DownloadResult:
        ...

    def describe(self) -> dict:
        return {"id": self.id, "name": self.name, "domains": list(self.domains)}
