"""Pydantic schemas for the media library API."""
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Quality = Literal["best", "1080p", "720p", "480p"]


class ResolveRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1)


class DownloadRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1)
    quality: Quality = "best"


class VideoRetentionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    retention_days: int = Field(..., le=3650, strict=True)

    @field_validator("retention_days")
    @classmethod
    def nonzero_retention(cls, value):
        if value == 0:
            raise ValueError("Retention days cannot be zero; use a negative number to keep this video indefinitely.")
        return value
