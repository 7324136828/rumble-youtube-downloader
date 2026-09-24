"""Persisted options for new library downloads."""
from fastapi import APIRouter, HTTPException, Path, Query

from ..schemas.settings import DownloadSettingsPatch, LinkSettingsPatch, LinkStatePatch
from ..services import db, library, recommendations

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/downloads")
def get_download_settings():
    return db.get_download_settings()


@router.patch("/downloads")
def update_download_settings(body: DownloadSettingsPatch):
    changes = body.model_dump(exclude_unset=True)
    try:
        result = db.update_download_settings(changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if changes.keys() & {"retention_days", "auto_delete_enabled"}:
        library.purge_expired()
    return result


@router.get("/links")
def get_link_settings(state: str = Query("all"), limit: int = Query(200, ge=1, le=200),
                      offset: int = Query(0, ge=0, le=2 ** 63 - 1)):
    try:
        return {**db.get_link_settings(), **db.list_link_history(state, limit, offset)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/links")
def update_link_settings(body: LinkSettingsPatch):
    result = db.update_link_settings(body.model_dump(exclude_unset=True))
    recommendations.clear_cache()
    return result


@router.patch("/links/{link_id}")
def update_link_state(body: LinkStatePatch,
                      link_id: int = Path(ge=1, le=2 ** 63 - 1)):
    row = db.update_link_state(link_id, body.state)
    if row is None:
        raise HTTPException(status_code=404, detail="Link not found")
    recommendations.clear_cache()
    return row


@router.delete("/links/{link_id}")
def delete_recorded_link(link_id: int = Path(ge=1, le=2 ** 63 - 1)):
    if not db.delete_link_history(link_id):
        raise HTTPException(status_code=404, detail="Link not found")
    recommendations.clear_cache()
    return {"deleted": True, "id": link_id}
