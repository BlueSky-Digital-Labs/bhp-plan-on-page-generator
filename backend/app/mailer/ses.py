"""Amazon SES delivery (SESv2 `SendEmail` with a raw message, so the PDFs can be attached)."""

import logging

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
            code = str(exc.response.get("Error", {}).get("Code", ""))
            logger.error(
                "email_send_failed",
                extra={"exception_type": type(exc).__name__, "failure_code": code[:60]},
            )
            raise MailerError(
                "rejected" if code in _REJECTED else "throttled" if code in _THROTTLED else "failed"
            ) from None
        except BotoCoreError as exc:
            logger.error("email_send_failed", extra={"exception_type": type(exc).__name__})
            raise MailerError("failed") from None
