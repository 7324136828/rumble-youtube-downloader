"""Persisted options for new library downloads."""
from fastapi import APIRouter

from ..schemas.settings import DownloadSettingsPatch
from ..services import db

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/downloads")
def get_download_settings():
    return db.get_download_settings()


@router.patch("/downloads")
def update_download_settings(body: DownloadSettingsPatch):
    return db.update_download_settings(body.model_dump(exclude_unset=True))
