"""Local recommendation settings, Connector discovery, and next-video results."""
import logging
import mimetypes
from pathlib import Path as FilePath

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import FileResponse

from ..schemas.recommendations import (RecommendationConfigImport, RecommendationRequest,
                                      RecommendationSettingsPatch, ToolInvocation, WatchLaterImport,
                                      WatchLaterPageImport)
from ..services import (connector_client, custom_website_search, db, recommendation_tools, recommendations,
                        page_link_import, recommendation_providers, video_redirects, watch_later_thumbnails,
                        watch_later_titles)
from .. import config

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])
_LOG = logging.getLogger(__name__)


@router.get("/settings")
def get_settings():
    return recommendations.get_settings()


@router.patch("/settings")
def patch_settings(body: RecommendationSettingsPatch):
    try:
        return recommendations.update_settings(body)
    except db.SettingsConflictError as exc:
        raise HTTPException(status_code=409, detail="Recommendation settings changed. Apply your selection again.") from exc
    except recommendations.RecommendationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except connector_client.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/providers/{provider_id}/thumbnail-domains/detect")
def detect_thumbnail_domains(provider_id: str = Path(min_length=1, max_length=253,
                                                     pattern=r"^[a-z0-9][a-z0-9.-]*$")):
    configured = recommendation_providers.configured_providers(
        db.get_recommendation_settings()["providers"], enabled_only=False)
    provider = next((item for item in configured if item["id"] == provider_id), None)
    if provider is None or provider["id"] in ("youtube", "rumble"):
        raise HTTPException(status_code=404, detail="Custom recommendation website not found.")
    try:
        return custom_website_search.discover_thumbnail_domains(provider)
    except custom_website_search.CustomSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/models")
def get_models():
    try:
        return connector_client.discover_models()
    except connector_client.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/watch-later")
def get_watch_later(source: str = Query("all", min_length=1, max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*$"),
                    limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0, le=2 ** 63 - 1)):
    return db.list_watch_later(source, limit, offset)


@router.post("/watch-later")
def save_watch_later(body: WatchLaterImport):
    try:
        videos = [video.model_dump(exclude_unset=True) for video in body.videos]
        if body.resolve_redirects:
            videos = video_redirects.resolve_import(videos, db.get_recommendation_settings()["providers"])
        result = db.add_watch_later(videos)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.fetch_titles:
        result["items"] = watch_later_titles.schedule_items(result["items"])
    else:
        result["items"] = watch_later_thumbnails.schedule_items(result["items"])
    return result


@router.post("/watch-later/{catalog_id}/title")
def fetch_watch_later_title(catalog_id: int = Path(ge=1, le=2 ** 63 - 1), force: bool = Query(False)):
    try:
        return watch_later_titles.request_title(catalog_id, force=force)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/watch-later/{catalog_id}/thumbnail")
def get_watch_later_thumbnail(catalog_id: int = Path(ge=1, le=2 ** 63 - 1)):
    value = db.get_watch_later_thumbnail(catalog_id)
    if not value:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    root = (config.JOBS_ROOT / "watch_later_thumbnails").resolve()
    path = FilePath(value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg")


@router.get("/watch-later/links")
def get_watch_later_links(limit: int = Query(200, ge=1, le=200),
                          offset: int = Query(0, ge=0, le=2 ** 63 - 1)):
    return db.list_watch_later_links(limit, offset)


@router.post("/watch-later/preview-links")
def preview_page_links(body: WatchLaterPageImport):
    """Extract page links for review without changing either saved collection."""
    try:
        extracted = page_link_import.extract_links(body.page_url)
        configured = recommendation_providers.configured_providers(
            db.get_recommendation_settings()["providers"], enabled_only=False)
        items = []
        for item in extracted["links"]:
            normalized = recommendation_tools.canonical_video_url(item["url"], configured)
            items.append({**item, "is_video": normalized is not None,
                          "video_url": normalized[1] if normalized else None,
                          "provider": normalized[0] if normalized else None})
        return {"page_url": extracted["page_url"], "items": items,
                "total": len(items), "video_total": sum(item["is_video"] for item in items)}
    except page_link_import.PageLinkImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except page_link_import.PageLinkUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/watch-later/save-all-links")
def save_all_page_links(body: WatchLaterPageImport):
    try:
        extracted = page_link_import.extract_links(body.page_url)
        result = db.save_watch_later_links(extracted["page_url"], extracted["links"])
        return {**result, "page_url": extracted["page_url"]}
    except page_link_import.PageLinkImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except page_link_import.PageLinkUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/watch-later/links/{link_id}")
def delete_watch_later_link(link_id: int = Path(ge=1, le=2 ** 63 - 1)):
    return db.remove_watch_later_link(link_id)


@router.delete("/watch-later/{catalog_id}")
def delete_watch_later(catalog_id: int = Path(ge=1, le=2 ** 63 - 1)):
    watch_later_thumbnails.remove_local(catalog_id)
    return db.remove_watch_later(catalog_id)


@router.post("/configs", status_code=201)
def import_config(body: RecommendationConfigImport):
    try:
        return connector_client.import_config(body.name, body.config)
    except connector_client.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/tools")
def list_tools():
    return {"tools": recommendation_tools.TOOLS}


@router.post("/tools/{name}")
def invoke_tool(name: str, body: ToolInvocation):
    try:
        return recommendations.invoke_tool(name, body.arguments)
    except (recommendations.RecommendationError, recommendation_tools.ToolError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def get_recommendations(body: RecommendationRequest):
    try:
        return recommendations.recommend(body)
    except (connector_client.ConnectorError, recommendation_tools.ToolError,
            recommendations.RecommendationError) as exc:
        # These exception messages are controlled/sanitized by the services.
        # Do not log prompts, watch history, raw model output, or exception causes.
        _LOG.warning("Recommendation request failed [%s/%s, upstream_status=%s]: %s",
                     type(exc).__name__, getattr(exc, "code", "invalid_recommendation"),
                     getattr(exc, "status_code", None), str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
