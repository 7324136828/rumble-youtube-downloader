"""Metadata returned by public platform search."""
from typing import Literal

from pydantic import BaseModel

SearchSource = Literal["all", "youtube", "rumble"]
VideoSource = Literal["youtube", "rumble"]


class SearchResult(BaseModel):
    id: str
    title: str
    source_url: str
    connector: VideoSource
    uploader: str | None = None
    duration: float | None = None
    thumbnail_url: str | None = None


class SearchWarning(BaseModel):
    source: VideoSource
    message: str


class SearchResponse(BaseModel):
    query: str
    source: SearchSource
    results: list[SearchResult]
    warnings: list[SearchWarning]
