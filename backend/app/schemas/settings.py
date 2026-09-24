"""Preferences for processing, retention, and discovered links."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_cookie_file_path(value: str) -> str:
    """Validate a backend-local path without opening or reading its contents."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Cookie file path must be text no longer than 2048 characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Cookie file path cannot contain control characters.")
    value = value.strip()
    # Windows' Copy as path includes quotes, which are not part of the filename.
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1].strip()
    if value and not Path(value).is_absolute():
        raise ValueError("Enter an absolute cookie file path on the computer running the backend.")
    return value


class DownloadSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    convert_for_browser: bool = False
    generate_thumbnails: bool = True
    auto_delete_enabled: bool = True
    retention_days: int = Field(default=7, le=3650, strict=True)
    cookie_browser: Literal["", "brave", "chrome", "chromium", "edge", "firefox",
                            "opera", "safari", "vivaldi", "whale"] = ""
    cookie_browser_profile: str = Field(default="", max_length=500)
    cookie_file: str = Field(default="", max_length=2048)

    @field_validator("retention_days")
    @classmethod
    def nonzero_retention(cls, value):
        if value == 0:
            raise ValueError("Retention days cannot be zero; use a negative number to keep videos indefinitely.")
        return value

    @field_validator("cookie_browser_profile")
    @classmethod
    def clean_cookie_profile(cls, value):
        if any(ord(character) < 32 for character in value):
            raise ValueError("Browser profile cannot contain control characters.")
        return value.strip()

    @field_validator("cookie_file")
    @classmethod
    def clean_cookie_file(cls, value):
        return normalize_cookie_file_path(value)


class LinkSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hide_repeated_links: bool


class LinkStatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["active", "allowed", "silenced", "excluded"]
