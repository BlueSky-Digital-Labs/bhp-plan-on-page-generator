"""Amazon SES delivery (SESv2 `SendEmail` with a raw message, so the PDFs can be attached)."""

import logging
import os
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.mailer.message import MailerError, OutgoingEmail, to_mime

logger = logging.getLogger("app.mailer")

# Provider error codes mapped to the three outcomes the app tells the user about. Nothing the
# provider says is passed on.
_REJECTED = {
    "MessageRejected",
    "MailFromDomainNotVerifiedException",
    "AccountSuspendedException",
    "SendingPausedException",
    "NotFoundException",
    "BadRequestException",
}
_THROTTLED = {
    "TooManyRequestsException",
    "LimitExceededException",
    "Throttling",
    "ThrottlingException",
}


def _denied_text(code: str, message: object) -> str | None:
    """AWS's own words for an access denial, only with EMAIL_LOG_ERROR_TEXT=true (test use).

    The message names the role, the action, the resource and the reason (for example an explicit
    deny in a service control policy). It can include a sender or recipient address, so it is off
    by default and must not stay on once real data is used. Control characters removed, 400 cut."""
    if os.environ.get("EMAIL_LOG_ERROR_TEXT", "").strip().lower() != "true":
        return None
    if code != "AccessDeniedException" or not isinstance(message, str):
        return None
    return re.sub(r"[\x00-\x1f\x7f-\x9f\s]+", " ", message).strip()[:400] or None


def build_ses_client(region: str):
    """SESv2 client on the default credential chain (the task role). One attempt only: SES does not
    de-duplicate, so a retry after a lost reply could deliver the same email twice."""
    return boto3.client(
        "sesv2",
        region_name=region,
        config=Config(
            connect_timeout=5,
            read_timeout=20,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )


class SesMailer:
    ready = True

    def __init__(self, client, sender: str, configuration_set: str | None = None) -> None:
        self._client, self._sender, self._configuration_set = client, sender, configuration_set

    def send(self, email: OutgoingEmail) -> None:
        raw = to_mime(self._sender, email)
        request = {
            "FromEmailAddress": self._sender,
            "Destination": {"ToAddresses": list(email.recipients)},
            "Content": {"Raw": {"Data": raw}},
        }
        if self._configuration_set:
            request["ConfigurationSetName"] = self._configuration_set
        try:
            self._client.send_email(**request)
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = str(error.get("Code", ""))
            meta = exc.response.get("ResponseMetadata", {})
            extra = {
                "exception_type": type(exc).__name__,
                "failure_code": code[:60],
                "http_status": meta.get("HTTPStatusCode"),
                "request_id": meta.get("RequestId"),
            }
            text = _denied_text(code, error.get("Message"))
            if text:
                extra["error_text"] = text
            logger.error("email_send_failed", extra=extra)
            raise MailerError(
                "rejected" if code in _REJECTED else "throttled" if code in _THROTTLED else "failed"
            ) from None
        except BotoCoreError as exc:
            logger.error("email_send_failed", extra={"exception_type": type(exc).__name__})
            raise MailerError("failed") from None
