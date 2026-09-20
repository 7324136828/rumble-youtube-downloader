"""Preferences for optional processing of new library downloads."""
from pydantic import BaseModel, ConfigDict


class DownloadSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    convert_for_browser: bool = False
    generate_thumbnails: bool = True
