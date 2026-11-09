"""What an email is made of, and the rules that keep it safe to send.

Pure code: no AWS here. Recipients are checked strictly (plain ASCII addresses only), every header
value is made single-line, and the user's own words go in the body as plain text, never as markup.
"""

import re
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

MAX_RECIPIENTS = 5
MAX_BODY_CHARS = 500  # the fixed text of the submission email
# What the email says unless the operator sets other text (see mailer/settings.py).
DEFAULT_BODY = (
    "SCP AI Ideation assistant submits a one page canvas for job development "
    "and with the 2 attachments"
)
MAX_BYTES = 10 * 1024 * 1024  # the two PDFs together; SES itself allows far more
_SUBJECT_NAME_CHARS = 80

# A plain address: ASCII, no display name, no quotes, comments or angle brackets. Anything cleverer
# is refused rather than interpreted, because the address ends up in message headers.
_ADDRESS = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@(?:[A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")
_BODY_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # all but tab and line breaks


class MailerError(Exception):
    """A send that did not happen. `code` is one of the fixed values below; never provider text."""

    CODES = ("unavailable", "rejected", "throttled", "failed", "too_large")

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    media_type: str


@dataclass(frozen=True)
class OutgoingEmail:
    recipients: tuple[str, ...]
    subject: str
    body: str
    attachments: tuple[Attachment, ...]


class Mailer(Protocol):
    ready: bool

    def send(self, email: OutgoingEmail) -> None:
        """Hand the message to the mail service, or raise MailerError. Never retries by itself:
        a retried send that already went through would deliver the email twice."""
        ...


class UnavailableMailer:
    """Email is not switched on. Nothing is sent and nothing is pretended."""

    ready = False

    def send(self, email: OutgoingEmail) -> None:
        raise MailerError("unavailable")


def is_address(value: str) -> bool:
    return len(value) <= 254 and _ADDRESS.fullmatch(value) is not None


def clean_recipients(values: list[str], allowed_domains: tuple[str, ...]) -> tuple[str, ...]:
    """The addresses to send to: trimmed, de-duplicated, and each one checked.

    Raises ValueError('invalid_recipient') for anything that is not a plain address (or for none at
    all or too many), and ValueError('recipient_not_allowed') for a domain outside the allow-list.
    An empty allow-list means any domain (local and test use only; deployed config requires one).
    """
    seen: dict[str, str] = {}
    for raw in values:
        value = raw.strip()
        if not is_address(value):
            raise ValueError("invalid_recipient")
        seen.setdefault(value.casefold(), value)
    if not 1 <= len(seen) <= MAX_RECIPIENTS:
        raise ValueError("invalid_recipient")
    if allowed_domains and any(a.rsplit("@", 1)[1].casefold() not in allowed_domains for a in seen):
        raise ValueError("recipient_not_allowed")
    return tuple(seen.values())


def single_line(value: str, limit: int) -> str:
    """Text that is safe inside a header: one line, no control characters, bounded."""
    return _CONTROL.sub(" ", value).strip()[:limit].strip()


def clean_body(text: str) -> str:
    """The email's text: line breaks kept, control characters removed, bounded. Plain text only."""
    text = _BODY_CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    return text[:MAX_BODY_CHARS].strip()


def compose(
    recipients: tuple[str, ...],
    solution_name: str,
    body: str,
    attachments: tuple[Attachment, ...],
    app_name: str = "Plan on a Page",
) -> OutgoingEmail:
    """The submission email for a confirmed canvas: a fixed subject naming the solution, the
    configured text as the whole body, and the attachments. Nothing the user typed is in it."""
    name = single_line(solution_name, _SUBJECT_NAME_CHARS)
    subject = f"{app_name}: {name}" if name else app_name
    text = clean_body(body) or DEFAULT_BODY
    return OutgoingEmail(recipients, subject, text + "\n", attachments)


def to_mime(sender: str, email: OutgoingEmail) -> bytes:
    """The raw message. Raises MailerError('too_large') if the attachments are over the cap."""
    if sum(len(a.content) for a in email.attachments) > MAX_BYTES:
        raise MailerError("too_large")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(email.recipients)
    message["Subject"] = single_line(email.subject, 200)
    message.set_content(email.body)
    for item in email.attachments:
        main, _, sub = item.media_type.partition("/")
        message.add_attachment(
            item.content, maintype=main, subtype=sub, filename=single_line(item.filename, 120)
        )
    return message.as_bytes()
