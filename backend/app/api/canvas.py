"""Download the one-page canvas as an editable PowerPoint slide."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.access.store import Session
from app.api.access import current_session
from app.api.conversation import get_conversation
from app.appendix.model import AppendixError
from app.canvas_export import PPTX_MEDIA_TYPE, render_canvas_pptx
from app.conversation.service import ConversationService
from app.errors import ApiProblem

router = APIRouter()
logger = logging.getLogger("app.canvas")


@router.get("/canvas-file")
def download_canvas(
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
    file_format: Annotated[Literal["pptx"], Query(alias="format")],
) -> Response:
    """Only a canvas the server calls ready can be downloaded. Built per request, never kept."""
    try:
        snapshot = service.export_snapshot(session)
        content = render_canvas_pptx(snapshot.canvas)
    except AppendixError as exc:
        if exc.code == "changed":
            raise ApiProblem(
                409, "conversation_changed", "Your conversation changed. Please try again."
            ) from None
        raise ApiProblem(409, "canvas_not_ready", "The canvas is not complete yet.") from None
    except Exception as exc:  # noqa: BLE001 - never leak renderer detail or user text
        logger.error("canvas_render_failed", extra={"exception_type": type(exc).__name__})
        raise ApiProblem(500, "canvas_failed", "The canvas could not be created.") from None
    logger.info("canvas_downloaded", extra={"reason": file_format})
    return Response(
        content=content,
        media_type=PPTX_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="plan-on-a-page.pptx"',
            "Cache-Control": "no-store",
            "X-Appendix-Revision": str(snapshot.revision),
        },
    )
