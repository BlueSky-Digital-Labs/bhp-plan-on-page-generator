"""Who a submission goes to and what it says: read at run time, so an operator can change either
without a deploy.

In AWS both live in Parameter Store (plain String parameters, not secrets) and are re-read at most
once a minute. Locally they come from settings. The recipient is checked on every read against the
allow-list of domains, so a mistyped or unexpected value stops submissions instead of sending them.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.mailer.message import DEFAULT_BODY, clean_body, clean_recipients

logger = logging.getLogger("app.mailer")

REFRESH_SECONDS = 60.0  # how long a good read is kept
RETRY_SECONDS = 10.0  # how soon to try again after a failed read


def build_ssm_client(region: str):
    """Systems Manager client on the default credential chain (the task role). Short timeouts: a
    slow read must not hold up a submission, and a failed one keeps the last good value."""
    return boto3.client(
        "ssm",
        region_name=region,
        config=Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 2}),
    )


@dataclass(frozen=True)
class SubmitSettings:
    recipient: str
    body: str


class SubmitSettingsSource(Protocol):
    def current(self) -> SubmitSettings | None:
        """The settings to use now, or None when submissions cannot be sent."""
        ...


class NoSubmitSettings:
    """Nothing is configured: submissions are unavailable."""

    def current(self) -> SubmitSettings | None:
        return None


class StaticSubmitSettings:
    """Fixed at start-up (local use and tests). The recipient is checked once, here."""

    def __init__(self, recipient: str, body: str = "", allowed_domains: tuple[str, ...] = ()):
        (checked,) = clean_recipients([recipient], allowed_domains)
        self._settings = SubmitSettings(checked, clean_body(body) or DEFAULT_BODY)

    def current(self) -> SubmitSettings | None:
        return self._settings


class ParameterStoreSubmitSettings:
    """The recipient (and optionally the text) from two String parameters, cached for a minute.

    A failed read keeps serving the last good value; with none yet, submissions are unavailable.
    A value that is not an allowed plain address is treated as no recipient, never sent to."""

    def __init__(
        self,
        client: Any,
        recipient_name: str,
        body_name: str | None = None,
        allowed_domains: tuple[str, ...] = (),
        *,
        refresh_seconds: float = REFRESH_SECONDS,
        retry_seconds: float = RETRY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client, self._names = client, [n for n in (recipient_name, body_name) if n]
        self._recipient_name, self._body_name = recipient_name, body_name
        self._domains = allowed_domains
        self._refresh, self._retry, self._clock = refresh_seconds, retry_seconds, clock
        self._lock = threading.Lock()
        self._value: SubmitSettings | None = None
        self._next_read = 0.0

    def current(self) -> SubmitSettings | None:
        with self._lock:
            if self._clock() >= self._next_read:
                self._read()
            return self._value

    def _read(self) -> None:
        try:
            response = self._client.get_parameters(Names=self._names)
        except (ClientError, BotoCoreError) as exc:
            logger.error("email_settings_unavailable", extra={"exception_type": type(exc).__name__})
            self._next_read = self._clock() + self._retry  # keep the last good value, if any
            return
        found = {p["Name"]: str(p.get("Value", "")) for p in response.get("Parameters", [])}
        self._next_read = self._clock() + self._refresh
        recipient = found.get(self._recipient_name, "").strip()
        try:
            (checked,) = clean_recipients([recipient], self._domains)
        except ValueError:
            logger.error("email_recipient_invalid", extra={"reason": "missing_or_not_allowed"})
            self._value = None
            return
        body = clean_body(found.get(self._body_name or "", "")) or DEFAULT_BODY
        self._value = SubmitSettings(checked, body)
