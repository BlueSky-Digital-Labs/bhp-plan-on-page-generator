"""Confirm the canvas after a read-back, then download it as a PDF or PowerPoint slide."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.access.store import Session
from app.api.access import current_session, require_csrf
from app.api.conversation import get_conversation
from app.api.exports import (
    attachment,
    delivery_recorder,
    downloadable_snapshot,
    ensure_still_current,
    problem,
)
from app.appendix.model import AppendixError
from app.canvas_export import (
    PDF_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    render_canvas_pdf,
    render_canvas_pptx,
)
from app.conversation.service import ConversationService

router = APIRouter()
logger = logging.getLogger("app.canvas")

_CONFIRM_FAILURES = {
    "not_ready": (422, "canvas_incomplete", "The canvas is not complete yet."),
    "unfinished": (409, "turn_in_progress", "The assistant has not finished replying yet."),
    "stale": (
        409,
        "revision_conflict",
        "The canvas has changed since it was shown. Please review it again.",
    ),
    "changed": (409, "conversation_changed", "Your conversation changed. Please try again."),
}
_FILES = {
    "pdf": (render_canvas_pdf, PDF_MEDIA_TYPE),
    "pptx": (render_canvas_pptx, PPTX_MEDIA_TYPE),
}


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The revision the user was shown and read back. Nothing else can be confirmed.
    revision: Annotated[int, Field(ge=0)]


@router.post("/canvas/confirm", dependencies=[Depends(require_csrf)])
def confirm_canvas(
    body: ConfirmBody,
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
) -> dict:
    """The user confirms the revision they read back. Returns the refreshed review."""
    try:
        service.confirm(session, body.revision)
    except AppendixError as exc:
        raise problem(exc, _CONFIRM_FAILURES) from None
    logger.info("canvas_confirmed")
    return service.history(session)


@router.get("/canvas-file")
def download_canvas(
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
    file_format: Annotated[Literal["pdf", "pptx"], Query(alias="format")],
    revision: Annotated[int, Query(ge=0)],
) -> Response:
    """The confirmed canvas. Built per request, in memory, and never kept."""
    snapshot = downloadable_snapshot(service, session, revision)
    render, media_type = _FILES[file_format]
    try:
        content = render(snapshot.canvas)
    except Exception as exc:  # noqa: BLE001 - never leak renderer detail or user text
        logger.error("canvas_render_failed", extra={"exception_type": type(exc).__name__})
        raise problem(AppendixError("render_failed")) from None
    ensure_still_current(service, session, revision)
    logger.info("canvas_downloaded", extra={"reason": file_format})
    recorder = delivery_recorder(service, session, "canvas", snapshot)
    return attachment(content, media_type, snapshot, "canvas", file_format, recorder)
