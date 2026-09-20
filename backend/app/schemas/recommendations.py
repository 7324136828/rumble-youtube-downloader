"""Validated inputs for local recommendation preferences and requests."""
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Keyword = Annotated[str, Field(min_length=1, max_length=100)]


class RecommendationSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool | None = None
    model_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    seed_keywords: list[Keyword] | None = Field(default=None, max_length=6)

    @model_validator(mode="after")
    def nonnull_preferences(self):
        for field in ("enabled", "seed_keywords"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self

    @field_validator("seed_keywords")
    @classmethod
    def clean_keywords(cls, value):
        if value is None:
            return value
        cleaned = list(dict.fromkeys(" ".join(word.split()) for word in value))
        if any(not word for word in cleaned):
            raise ValueError("Keywords cannot be blank")
        return cleaned


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: Literal["feed", "watch"] = "feed"
    video_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    exclude_urls: list[Annotated[str, Field(max_length=2048)]] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=8, ge=1, le=20)
    refresh: bool = False


class RecommendationConfigImport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    config: dict

    @model_validator(mode="after")
    def bounded_config(self):
        self.name = self.name.strip()
        if not self.name or len(json.dumps(self.config).encode("utf-8")) > 128 * 1024:
            raise ValueError("Provide a name and a config smaller than 128 KiB")
        return self


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    arguments: dict

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, value):
        if len(json.dumps(value).encode("utf-8")) > 8192:
            raise ValueError("Tool arguments exceed 8 KiB")
        return value
