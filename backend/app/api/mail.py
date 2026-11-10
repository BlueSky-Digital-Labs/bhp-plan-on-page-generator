"""Submit the confirmed canvas and its appendix by email, both as PDFs the server builds itself.

The browser sends only the revision. The recipient and the text come from the server's settings
(Parameter Store in AWS, so an operator can change them without a deploy); nobody using the app
can choose an address or add text."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.access.store import Session
from app.api.access import active_session, require_csrf
from app.api.conversation import get_conversation
from app.api.exports import (
    appendix_bytes,
    confirmed_snapshot,
    document_filename,
    ensure_still_confirmed,
    problem,
)
from app.appendix import MEDIA_TYPES, AppendixError
from app.canvas_export import render_canvas_pdf
from app.config import Settings, get_settings
from app.conversation.service import ConversationService
from app.errors import ApiProblem
from app.mailer import Attachment, MailerError, compose
from app.mailer.limits import SendRefused
from app.mailer.service import MailService

router = APIRouter()
logger = logging.getLogger("app.mailer")

# Fixed, content-free wording for every way a send can fail. Addresses are never echoed.
_REFUSED = {
    "in_progress": (409, "send_in_progress", "An email is already being sent."),
    "limit": (429, "email_limit", "The number of emails allowed for this session has been used."),
    "full": (503, "email_unavailable", "Email is not available right now."),
}
_MAILER_FAILURES = {
    "unavailable": (503, "email_unavailable", "Email is not available right now."),
    "rejected": (502, "email_rejected", "The email service did not accept the message."),
    "throttled": (503, "email_busy", "The email service is busy. Please try again shortly."),
    "failed": (502, "email_failed", "The email could not be sent."),
    "too_large": (413, "email_too_large", "The documents are too large to email."),
}


def get_mail(request: Request) -> MailService:
    return request.app.state.mail


class EmailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The revision the user confirmed; nothing else can be sent, and nothing else is accepted.
    revision: Annotated[int, Field(ge=0)]


@router.post("/email", dependencies=[Depends(require_csrf)])
def send_email(
    body: EmailBody,
    session: Annotated[Session, Depends(active_session)],
    service: Annotated[ConversationService, Depends(get_conversation)],
    mail: Annotated[MailService, Depends(get_mail)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """Builds the canvas and appendix PDFs for the confirmed revision and emails them to the one
    configured recipient, with the configured text.

    Same gate as the downloads (current, complete and confirmed), checked again just before the
    send. Only the configured sender and recipient are used, and nothing is stored.
    """
    submit = mail.submit.current()
    if submit is None or not mail.mailer.ready:
        raise ApiProblem(*_MAILER_FAILURES["unavailable"])

    try:
        mail.limiter.begin(session.session_id, session.expires_at)
    except SendRefused as exc:
        raise ApiProblem(*_REFUSED[exc.code]) from None
    sent = False
    try:
        snapshot = confirmed_snapshot(service, session, body.revision)
        try:
            canvas_pdf = render_canvas_pdf(snapshot.canvas)
        except Exception as exc:  # noqa: BLE001 - never leak renderer detail or user text
            logger.error("canvas_render_failed", extra={"exception_type": type(exc).__name__})
            raise problem(AppendixError("render_failed")) from None
        appendix_pdf = appendix_bytes(service, session, snapshot, "pdf", settings.app_name)
        ensure_still_confirmed(service, session, body.revision)
        email = compose(
            (submit.recipient,),
            str(snapshot.canvas.get("solutionName") or ""),
            submit.body,
            (
                Attachment(
                    document_filename(snapshot, "canvas", "pdf"), canvas_pdf, MEDIA_TYPES["pdf"]
                ),
                Attachment(
                    document_filename(snapshot, "appendix", "pdf"), appendix_pdf, MEDIA_TYPES["pdf"]
                ),
            ),
            settings.app_name,
        )
        try:
            mail.mailer.send(email)
        except MailerError as exc:
            raise ApiProblem(*_MAILER_FAILURES.get(exc.code, _MAILER_FAILURES["failed"])) from None
        sent = True
    finally:
        mail.limiter.finish(session.session_id, session.expires_at, sent=sent)
    # The documents have left the building, so the session may now be deleted, as after a download.
    for kind in ("canvas", "appendix"):
        try:
            service.record_delivery(session, kind, snapshot.revision)
        except Exception as exc:  # noqa: BLE001 - the email is sent; only the unlock is lost
            logger.error("delivery_not_recorded", extra={"exception_type": type(exc).__name__})
    logger.info("email_sent", extra={"detail": "recipients=1"})
    return {"sent": 1}
