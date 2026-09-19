"""Pydantic schemas for the media library API."""
from typing import List, Literal

from pydantic import BaseModel, Field

Quality = Literal["best", "1080p", "720p", "480p"]


class ResolveRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1)


class DownloadRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1)
    quality: Quality = "best"
