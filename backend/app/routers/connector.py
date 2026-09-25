"""Local setup, activity observations, and authenticated Connector tool callbacks."""
import json
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ..schemas.connector_bridge import BridgeSettingsPatch, RecommendationImpressions
from ..services import connector_activity, connector_bridge, connector_client, connector_tools, db

router = APIRouter(prefix="/api/connector", tags=["connector"])


@router.get("/status")
def get_status():
    return connector_bridge.status()


@router.patch("/settings")
def patch_settings(body: BridgeSettingsPatch):
    return connector_bridge.configure(body.model_dump(exclude_unset=True))


@router.post("/connect")
def connect():
    if not connector_activity.get_state()["enabled"]:
        raise HTTPException(status_code=409, detail="Enable Connector integration first.")
    try:
        return connector_bridge.sync_once(force=True)
    except connector_client.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/tools")
def tools():
    return {"tools": connector_tools.tool_definitions()}


@router.post("/impressions")
def record_impressions(body: RecommendationImpressions):
    try:
        recorded = connector_activity.record_impressions(
            [item.model_dump(exclude_none=True) for item in body.items],
            body.context, event_id=body.event_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"recorded": bool(recorded)}


@router.post("/hooks/{token}/{name}", include_in_schema=False)
async def invoke(token: str, name: str, request: Request):
    def authorize():
        state = connector_activity.get_state()
        if not state["enabled"] or not secrets.compare_digest(token.encode("utf-8"), state["token"].encode("utf-8")):
            raise HTTPException(status_code=403, detail="Connector tool access is disabled or unauthorized.")
        return state

    authorize()
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 64 * 1024:
            raise HTTPException(status_code=413, detail="Tool arguments exceed 64 KiB.")
        data.extend(chunk)
    try:
        arguments = json.loads(data)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object.")
        state = authorize()
        return await run_in_threadpool(connector_tools.dispatch, name, arguments, state["base_url"])
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid tool arguments: " + "; ".join(
            error["msg"] for error in exc.errors(include_input=False))) from exc
    except db.SettingsConflictError as exc:
        raise HTTPException(status_code=409, detail="Recommendation settings changed. Retry this tool.") from exc
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except connector_client.ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
