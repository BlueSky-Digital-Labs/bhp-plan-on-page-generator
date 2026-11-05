"""Download the appendix: the full answers and the conversation record behind the canvas."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.access.store import Session
from app.api.access import current_session
from app.api.conversation import get_conversation
from app.api.exports import (
    appendix_bytes,
    attachment,
    confirmed_snapshot,
    delivery_recorder,
    ensure_still_confirmed,
)
from app.appendix import MEDIA_TYPES, Format
from app.conversation.service import ConversationService

router = APIRouter()
logger = logging.getLogger("app.appendix")


@router.get("/appendix")
def download_appendix(
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
    file_format: Annotated[Format, Query(alias="format")],
    revision: Annotated[int, Query(ge=0)],
) -> Response:
    """The confirmed canvas's appendix. Built from this session's stored conversation on every
    request; nothing is kept."""
    snapshot = confirmed_snapshot(service, session, revision)
    content = appendix_bytes(service, session, snapshot, file_format)
    ensure_still_confirmed(service, session, revision)
    logger.info("appendix_downloaded", extra={"reason": file_format})
    recorder = delivery_recorder(service, session, "appendix", snapshot, file_format)
    return attachment(
        content, MEDIA_TYPES[file_format], snapshot, "appendix", file_format, recorder
    )
