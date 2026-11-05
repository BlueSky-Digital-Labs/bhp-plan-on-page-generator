"""Download status: which of the two documents the server has finished sending, and whether the
user may delete the session. Read-only; the same gate decides a deletion request."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.access.store import Session
from app.api.access import current_session
from app.api.conversation import get_conversation
from app.conversation.service import ConversationService

router = APIRouter()


@router.get("/downloads")
def download_status(
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
    response: Response,
) -> dict:
    """The caller's own status only. "Downloaded" means the server finished sending that PDF for
    this revision; it does not mean the browser kept it or the user saved it."""
    response.headers["Cache-Control"] = "no-store"
    return service.download_gate(session).view()
