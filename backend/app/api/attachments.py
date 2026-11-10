"""Read the text out of an attached file so the user can send it with their answer."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.access.store import Session
from app.api.access import active_session, require_csrf
from app.attachments.markers import block
from app.attachments.readers import MAX_BYTES, AttachmentError, extract
from app.errors import ApiProblem

router = APIRouter()
logger = logging.getLogger("app.attachments")

_FAILURES = {
    "unsupported_file_type": (
        415,
        "Only PDF, Word (.docx), PowerPoint (.pptx) and JSON files can be attached.",
    ),
    "file_unreadable": (422, "That file could not be read."),
    "file_has_no_text": (422, "No text was found in that file."),
}
_TOO_LARGE = ApiProblem(413, "file_too_large", "That file is too large.")


@router.post("/attachments", dependencies=[Depends(require_csrf)])
async def read_attachment(
    request: Request,
    session: Annotated[Session, Depends(active_session)],
    name: Annotated[str, Query(min_length=1, max_length=255)],
) -> dict:
    """The raw file is the request body. It is read in memory and discarded: nothing is stored, and
    only its text is returned for the browser to send as part of the user's own answer."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise _TOO_LARGE
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise _TOO_LARGE
    try:
        found = await run_in_threadpool(extract, name, bytes(data))
    except AttachmentError as exc:
        status, message = _FAILURES.get(exc.code, (422, "That file could not be read."))
        logger.info("attachment_refused", extra={"reason": exc.code})
        raise ApiProblem(status, exc.code, message) from None
    logger.info("attachment_read", extra={"reason": found.kind})
    return {
        "filename": found.name,
        "kind": found.kind,
        "characters": len(found.text),
        "truncated": found.truncated,
        "block": block(found.name, found.kind, found.text, truncated=found.truncated),
    }
