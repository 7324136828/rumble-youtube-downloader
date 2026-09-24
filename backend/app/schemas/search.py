"""Metadata returned by public platform search."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

SearchSource = Annotated[str, Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")]
VideoSource = SearchSource


class SearchResult(BaseModel):
    id: str
    title: str
    description: str | None = Field(default=None, max_length=500)
    source_url: str
    connector: VideoSource
    uploader: str | None = None
    duration: float | None = None
    thumbnail_url: str | None = None
    provider_name: str | None = Field(default=None, max_length=60)
    verified: bool = True
    verification: Literal["provider_search", "web_search", "custom_search"] = "provider_search"
    origins: list[Literal["custom_search", "public_search", "watch_later"]] = Field(default_factory=list)
    link_id: int | None = None


class SearchWarning(BaseModel):
    source: VideoSource
    message: str


class SearchResponse(BaseModel):
    query: str
    source: SearchSource
    results: list[SearchResult]
    warnings: list[SearchWarning]
    has_more: bool = False
