"""Local configuration and rendered recommendation observations for The Connector."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BridgeSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("enabled", "base_url")
    @classmethod
    def nonnull(cls, value):
        if value is None:
            raise ValueError("Integration settings cannot be null.")
        return value

    @field_validator("base_url")
    @classmethod
    def reachable_root(cls, value):
        value = value.strip().rstrip("/")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Use an HTTP(S) downloader address with a valid port.") from exc
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or parsed.hostname in ("0.0.0.0", "::")
                or any(ord(char) <= 32 for char in value)
                or (port is not None and port == 0)):
            raise ValueError("Use the reachable HTTP(S) downloader root, without credentials or a path.")
        return value


class ImpressionVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str | None = Field(default=None, max_length=2048)
    source_url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    connector: str | None = Field(default=None, max_length=253)
    uploader: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    thumbnail_url: str | None = Field(default=None, max_length=2048)

    @field_validator("source_url")
    @classmethod
    def http_source(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Video source must be an HTTP(S) URL.")
        return value


class RecommendationImpressions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    context: Literal["feed", "watch", "history"]
    items: list[ImpressionVideo] = Field(min_length=1, max_length=20)
