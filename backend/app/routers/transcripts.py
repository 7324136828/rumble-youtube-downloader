"""Fetch, prepare, and download transcripts for saved library media."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import transcripts

router = APIRouter(prefix="/api/media", tags=["transcripts"])


def _http_error(exc: Exception) -> HTTPException:
    status = 404 if isinstance(exc, LookupError) else 409 if isinstance(exc, RuntimeError) else 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/{video_id}/transcript")
def get_transcript(video_id: str):
    try:
        return transcripts.transcript_payload(video_id, include_text=True)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.post("/{video_id}/transcript", status_code=202)
def prepare_transcript(video_id: str):
    try:
        return transcripts.start_transcript(video_id)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.get("/{video_id}/transcript/download")
def download_transcript(video_id: str):
    try:
        path = transcripts.transcript_file(video_id)
    except (LookupError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc
    return FileResponse(path=path, filename=path.name, media_type="text/plain; charset=utf-8")
