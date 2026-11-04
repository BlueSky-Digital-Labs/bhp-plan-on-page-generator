"""Download the appendix: the full answers and the conversation record behind the canvas."""

import dataclasses
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.access.store import Session
from app.api.access import current_session
from app.api.conversation import get_conversation
from app.appendix import MEDIA_TYPES, AppendixError, Format, build_input, render
from app.conversation.service import ConversationService
from app.errors import ApiProblem

router = APIRouter()
logger = logging.getLogger("app.appendix")

_FAILURES = {
    "not_ready": (409, "canvas_not_ready", "The canvas is not complete yet."),
    "changed": (409, "conversation_changed", "Your conversation changed. Please try again."),
}


@router.get("/appendix")
def download_appendix(
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
    file_format: Annotated[Format, Query(alias="format")],
) -> Response:
    """Built from this session's stored conversation on every request; nothing is kept."""
    try:
        appendix = build_input(service.export_snapshot(session))
        overview = service.overview_for(session, appendix.source_revision, appendix.turns)
        if overview:
            summary = dataclasses.replace(appendix.summary, overview=overview)
            appendix = dataclasses.replace(appendix, summary=summary)
        content = render(file_format, appendix)
    except AppendixError as exc:
        status, code, message = _FAILURES.get(
            exc.code, (500, "appendix_failed", "The appendix could not be created.")
        )
        raise ApiProblem(status, code, message) from None
    logger.info("appendix_downloaded", extra={"reason": file_format})
    return Response(
        content=content,
        media_type=MEDIA_TYPES[file_format],
        headers={
            "Content-Disposition": f'attachment; filename="plan-on-a-page-appendix.{file_format}"',
            "Cache-Control": "no-store",
            "X-Appendix-Revision": str(appendix.source_revision),
        },
    )
