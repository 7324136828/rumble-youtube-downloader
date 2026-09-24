"""Validated inputs for local recommendation preferences and requests."""
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .recommendation_providers import RecommendationProvider, validate_provider_list

Keyword = Annotated[str, Field(min_length=1, max_length=100)]


class FallbackWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    custom_search: int = Field(default=50, ge=0, le=100)
    public_search: int = Field(default=0, ge=0, le=0)
    watch_later: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def total_percentage(self):
        if self.custom_search + self.public_search + self.watch_later != 100:
            raise ValueError("Fallback percentages must add up to 100")
        return self


def default_fallback_weights() -> dict:
    return FallbackWeights().model_dump()


class RecommendationSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool | None = None
    model_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    seed_keywords: list[Keyword] | None = Field(default=None, max_length=6)
    custom_prompt: str | None = Field(default=None, max_length=2000)
    allow_unverified_links: bool | None = None
    allow_ai_title_lookup: bool | None = None
    fetch_all_search_links: bool | None = None
    providers: list[RecommendationProvider] | None = Field(default=None, max_length=12)
    fallback_weights: FallbackWeights | None = None

    @model_validator(mode="after")
    def nonnull_preferences(self):
        for field in ("enabled", "seed_keywords", "custom_prompt", "allow_unverified_links", "allow_ai_title_lookup", "fetch_all_search_links", "providers", "fallback_weights"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.providers is not None:
            validate_provider_list(self.providers)
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

    @field_validator("custom_prompt")
    @classmethod
    def clean_custom_prompt(cls, value):
        if value is None:
            return value
        if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
            raise ValueError("The custom prompt cannot contain control characters")
        return value.strip()


class WatchLaterVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)

    @field_validator("source_url", "title", "description")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if value is not None else None


class WatchLaterImport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    videos: list[WatchLaterVideo] = Field(min_length=1, max_length=200)
    fetch_titles: bool = True
    resolve_redirects: bool = True


class WatchLaterPageImport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page_url: str = Field(min_length=1, max_length=2048)

    @field_validator("page_url")
    @classmethod
    def trim_page_url(cls, value):
        return value.strip()


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: Literal["feed", "watch", "history"] = "feed"
    source: Annotated[str, Field(min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$")] = "all"
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
