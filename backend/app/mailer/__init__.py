"""Sending the finished canvas and appendix by email (Amazon SES).

The server builds both PDFs itself for the confirmed revision and sends them from one configured
sender address. Nothing is stored. See docs/email.md.
"""

from app.mailer.message import (
    DEFAULT_BODY,
    MAX_BODY_CHARS,
    MAX_RECIPIENTS,
    Attachment,
    Mailer,
    MailerError,
    OutgoingEmail,
    UnavailableMailer,
    clean_body,
    clean_recipients,
    compose,
    to_mime,
)
from app.mailer.ses import SesMailer, build_ses_client

__all__ = [
    "DEFAULT_BODY",
    "MAX_BODY_CHARS",
    "MAX_RECIPIENTS",
    "Attachment",
    "Mailer",
    "MailerError",
    "OutgoingEmail",
    "SesMailer",
    "UnavailableMailer",
    "build_ses_client",
    "clean_body",
    "clean_recipients",
    "compose",
    "to_mime",
]
