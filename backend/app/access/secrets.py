"""Where the access-code hash and cookie-signing key come from.

Deployed: AWS Secrets Manager through the workload role. The access-code hash is fetched
on EVERY verification and never cached, so rotating the secret takes effect on the next
attempt without a restart. The signing key is cached briefly (it is not what June rotates).
Any lookup problem raises DependencyUnavailable: the caller fails closed.
"""

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from app.access.errors import DependencyUnavailable
from app.config import Secrets

MIN_KEY_BYTES = 32
KEY_CACHE_SECONDS = 60


class SecretProvider(Protocol):
    def access_code_hash(self) -> str: ...

    def cookie_signing_key(self) -> bytes: ...


def _checked_key(value: str | None) -> bytes:
    key = (value or "").encode()
    if len(key) < MIN_KEY_BYTES:
        raise DependencyUnavailable("signing_key_invalid")
    return key


class SecretsManagerProvider:
    def __init__(
        self,
        client: Any,
        hash_secret_id: str,
        key_secret_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client, self._hash_id, self._key_id, self._clock = (
            client,
            hash_secret_id,
            key_secret_id,
            clock,
        )
        self._key: tuple[bytes, float] | None = None
        self._key_lock = threading.Lock()  # requests run on a thread pool

    def _fetch(self, secret_id: str) -> str:
        try:
            return self._client.get_secret_value(SecretId=secret_id)["SecretString"]
        except (ClientError, BotoCoreError, KeyError):
            raise DependencyUnavailable("secret_lookup") from None

    def access_code_hash(self) -> str:
        return self._fetch(self._hash_id)  # deliberately uncached

    def cookie_signing_key(self) -> bytes:
        cached = self._key
        if cached is not None and cached[1] > self._clock():
            return cached[0]
        with self._key_lock:
            now = self._clock()
            cached = self._key  # another thread may have refreshed it while this one waited
            if cached is None or cached[1] <= now:
                cached = self._key = (
                    _checked_key(self._fetch(self._key_id)),
                    now + KEY_CACHE_SECONDS,
                )
            return cached[0]


class EnvSecretProvider:
    """Local development and tests only: values come from ACCESS_CODE_HASH / COOKIE_SIGNING_KEY."""

    def access_code_hash(self) -> str:
        value = Secrets().access_code_hash
        if value is None:
            raise DependencyUnavailable("secret_lookup")
        return value.get_secret_value()

    def cookie_signing_key(self) -> bytes:
        value = Secrets().cookie_signing_key
        return _checked_key(value.get_secret_value() if value else None)
