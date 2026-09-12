"""Pydantic request / response schemas."""
from typing import List, Optional

from pydantic import BaseModel, Field


class JobOptions(BaseModel):
    model: Optional[str] = None
    language: Optional[str] = None
    device: Optional[str] = None
    compute_type: Optional[str] = None
    workers: Optional[int] = None
    chunk_seconds: Optional[int] = None
    gpu_indices: Optional[List[int]] = None
    impersonate: Optional[str] = None
    keep_video: bool = False
    allow_video_fallback: bool = False
    audio_only: bool = False
    no_transcript: bool = False
    overwrite_transcript: bool = False
    no_vad: bool = False
    verbose: bool = False


class ConvertRequest(BaseModel):
    urls: List[str] = Field(..., min_length=1)
    filename: Optional[str] = None
    options: JobOptions = Field(default_factory=JobOptions)
