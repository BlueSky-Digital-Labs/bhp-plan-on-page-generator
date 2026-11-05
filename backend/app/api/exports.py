"""What every document download shares: the revision checks and the file name."""

import dataclasses
import logging
from collections.abc import Callable, Iterator

from fastapi.responses import StreamingResponse

from app.access.store import Session
from app.appendix import AppendixError, ExportSnapshot, Format, build_input, render
from app.canvas_export import safe_filename_stem
from app.conversation.service import ConversationService
from app.delivery import DocumentKind
from app.errors import ApiProblem

logger = logging.getLogger("app.downloads")
CHUNK_BYTES = 64 * 1024
# Only the PDF of each document counts towards the deletion gate (the ticket's "both PDFs").
COUNTED_FORMAT = "pdf"

# Document downloads: why a request was refused. Fixed, content-free wording.
DOWNLOAD_FAILURES = {
    "not_ready": (409, "canvas_not_ready", "The canvas is not complete yet."),
    "unfinished": (409, "turn_in_progress", "The assistant has not finished replying yet."),
    "stale": (
        409,
        "revision_conflict",
        "The canvas has changed since it was shown. Please review it again.",
    ),
    "not_confirmed": (409, "not_confirmed", "Confirm the canvas first."),
    "changed": (409, "conversation_changed", "Your conversation changed. Please try again."),
}


def problem(
    error: AppendixError, table: dict[str, tuple[int, str, str]] = DOWNLOAD_FAILURES
) -> ApiProblem:
    status, code, message = table.get(
        error.code, (500, "appendix_failed", "The document could not be created.")
    )
    return ApiProblem(status, code, message)


def confirmed_snapshot(
    service: ConversationService, session: Session, revision: int
) -> ExportSnapshot:
    """The canvas for a document: only the current revision, and only once the user confirmed it."""
    try:
        return service.confirmed_snapshot(session, revision)
    except AppendixError as exc:
        raise problem(exc) from None


def ensure_still_confirmed(service: ConversationService, session: Session, revision: int) -> None:
    """Checked again after the file is built and before it is sent, so an answer that arrived
    meanwhile cannot let an obsolete canvas through."""
    if not service.still_confirmed(session, revision):
        raise problem(AppendixError("stale"))


def appendix_bytes(
    service: ConversationService,
    session: Session,
    snapshot: ExportSnapshot,
    file_format: Format,
    app_name: str = "Plan on a Page",
) -> bytes:
    """The appendix file for a confirmed snapshot, with the AI overview when there is one."""
    try:
        appendix = build_input(snapshot, app_name)
        overview = service.overview_for(session, appendix.source_revision, appendix.turns)
        if overview:
            summary = dataclasses.replace(appendix.summary, overview=overview)
            appendix = dataclasses.replace(appendix, summary=summary)
        return render(file_format, appendix)
    except AppendixError as exc:
        raise problem(exc) from None


def document_filename(snapshot: ExportSnapshot, kind: str, ext: str) -> str:
    stem = safe_filename_stem(str(snapshot.canvas.get("solutionName") or ""))
    return f"{stem}-{kind}-r{snapshot.revision}.{ext}"  # ASCII letters, digits and hyphens


def delivery_recorder(
    service: ConversationService,
    session: Session,
    kind: DocumentKind,
    snapshot: ExportSnapshot,
    file_format: str,
) -> Callable[[], None] | None:
    """What to do once a file has been fully sent, if this format counts towards the gate."""
    if file_format != COUNTED_FORMAT:
        return None
    return lambda: service.record_delivery(session, kind, snapshot.revision)


def _stream(content: bytes, on_delivered: Callable[[], None] | None) -> Iterator[bytes]:
    """The file in chunks. `on_delivered` runs only after the last chunk has been handed over:
    not on arrival, not when headers go out, and not if the stream is cancelled or fails (the
    code after the loop is never reached then). It is a server-side boundary, nothing more."""
    for start in range(0, len(content), CHUNK_BYTES):
        yield content[start : start + CHUNK_BYTES]
    if on_delivered is not None:
        try:
            on_delivered()
        except Exception as exc:  # noqa: BLE001 - the file is already sent; never fail it now
            logger.error("delivery_record_failed", extra={"exception_type": type(exc).__name__})


def attachment(
    content: bytes,
    media_type: str,
    snapshot: ExportSnapshot,
    kind: str,
    ext: str,
    on_delivered: Callable[[], None] | None = None,
) -> StreamingResponse:
    filename = document_filename(snapshot, kind, ext)
    return StreamingResponse(
        _stream(content, on_delivered),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
            "Cache-Control": "no-store",
            "X-Appendix-Revision": str(snapshot.revision),
        },
    )
