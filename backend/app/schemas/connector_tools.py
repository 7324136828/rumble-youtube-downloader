"""Strict arguments advertised to The Connector's external agent tools."""
from datetime import datetime, timezone
import ipaddress
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..connectors.base import http_url_host
from .media import Quality
from .recommendations import RecommendationRequest, RecommendationSettingsPatch, WatchLaterVideo
from .search import SearchSource


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SearchVideos(ToolArguments):
    query: str = Field(min_length=1, max_length=200)
    source: SearchSource = "all"
    limit: int = Field(default=12, ge=1, le=24)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value):
        if not value.strip():
            raise ValueError("Search query cannot be blank")
        return value.strip()


class DownloadVideos(ToolArguments):
    urls: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(min_length=1, max_length=10)
    quality: Quality = "best"

    @field_validator("urls")
    @classmethod
    def valid_urls(cls, urls):
        cleaned = list(dict.fromkeys(url.strip() for url in urls))
        if any(not http_url_host(url) or "\\" in url or any(ord(c) < 32 for c in url) for url in cleaned):
            raise ValueError("Every download URL must be an HTTP(S) URL without credentials or control characters")
        for url in cleaned:
            host = http_url_host(url)
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if ("." not in host or host.rsplit(".", 1)[-1].isdigit()
                        or host.rsplit(".", 1)[-1] in {"localhost", "local", "internal", "home", "lan", "test", "invalid", "onion"}):
                    raise ValueError("Download URLs must use a public website")
            else:
                if not address.is_global or address.is_multicast or address.is_reserved:
                    raise ValueError("Download URLs must use a public website")
        return cleaned


class SetHint(ToolArguments):
    hint: str = Field(max_length=2000, description="Replace the saved recommendation hint; an empty string clears it.")

    @field_validator("hint")
    @classmethod
    def clean_hint(cls, value):
        return RecommendationSettingsPatch(custom_prompt=value).custom_prompt


class VideoIds(ToolArguments):
    video_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1, max_length=100)


class AddWatchLater(ToolArguments):
    videos: list[WatchLaterVideo] = Field(min_length=1, max_length=100)
    fetch_titles: bool = True


class GetTask(ToolArguments):
    task_id: str = Field(min_length=1, max_length=100)


class ListVideos(ToolArguments):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=2**31 - 1)
    order: Literal["oldest", "newest"] = "oldest"
    source: SearchSource = "all"
    query: str | None = Field(default=None, min_length=1, max_length=200,
                              description="Case-insensitive substring in title, description or uploader.")
    uploader: str | None = Field(default=None, min_length=1, max_length=200)
    date_from: str | None = Field(default=None, max_length=40, description="Inclusive ISO date/time; UTC when no timezone is given.")
    date_to: str | None = Field(default=None, max_length=40, description="Inclusive ISO date/time; a date alone includes that whole UTC day.")

    @field_validator("date_from", "date_to")
    @classmethod
    def valid_dates(cls, value, info):
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Use an ISO date or date/time") from exc
        if len(value) == 10 and info.field_name == "date_to":
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before date_to")
        return self


class DownloadedVideos(ListVideos):
    keyword: str | None = Field(default=None, min_length=1, max_length=100)


class WatchHistory(ListVideos):
    completed: bool | None = None


class RecommendationHistory(ListVideos):
    fallback: bool | None = None


class GenerateRecommendations(RecommendationRequest):
    """The existing recommendation request owns request-local filters."""


class RecentTags(ToolArguments):
    limit: int = Field(default=30, ge=1, le=100)


class ListActivity(ToolArguments):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=2**31 - 1)
    event_type: Literal["search", "watch", "recommendation_impressions"] | None = None
    date_from: str | None = Field(default=None, max_length=40)
    date_to: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_dates(self):
        validated = ListVideos(date_from=self.date_from, date_to=self.date_to)
        self.date_from, self.date_to = validated.date_from, validated.date_to
        return self
