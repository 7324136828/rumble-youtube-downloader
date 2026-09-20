"""Playback observations; video metadata is always looked up on the server."""
from pydantic import BaseModel, ConfigDict, Field


class WatchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    video_id: str = Field(min_length=1, max_length=100)
    position_seconds: float = Field(ge=0, le=604800)
    watched_seconds: float = Field(ge=0, le=30)
    completed: bool = False
