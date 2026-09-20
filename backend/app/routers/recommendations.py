"""Local recommendation settings, Connector discovery, and next-video results."""
import logging

from fastapi import APIRouter, HTTPException

from ..schemas.recommendations import (RecommendationConfigImport, RecommendationRequest,
                                      RecommendationSettingsPatch, ToolInvocation)
from ..services import connector_client, db, recommendation_tools, recommendations

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


@router.get("/models")
def get_models():
    try:
        return connector_client.discover_models()
    except connector_client.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
