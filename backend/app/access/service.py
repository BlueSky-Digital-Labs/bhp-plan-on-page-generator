"""Access rules in one place: code verification, grants, sessions.

Routes stay thin; every decision about who may do what is made here, so session lookup
and authorisation cannot be bypassed by checking only a cookie signature.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.access import cookies
from app.access.errors import AccessRejected, DependencyUnavailable
from app.access.secrets import SecretProvider
from app.access.store import Session, SessionStore

SESSION_LIFETIME = timedelta(hours=24)  # fixed at creation; activity never extends it
GRANT_LIFETIME = timedelta(minutes=10)
MAX_CODE_LENGTH = 128  # bounds the work an unauthenticated request can cause


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AccessService:
    def __init__(
        self,
        secret_provider: SecretProvider,
        store: SessionStore,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._secrets, self._store, self._clock = secret_provider, store, clock
        self._hasher = PasswordHasher()

    def verify_access_code(self, code: str) -> bool:
        """True only if `code` matches the CURRENT hash. The code is never stored or logged."""
        current_hash = self._secrets.access_code_hash()  # fetched every time
        try:
            return self._hasher.verify(current_hash, code)
        except VerifyMismatchError:
            return False
        except (VerificationError, InvalidHashError):
            # A malformed stored hash is a deployment fault: fail closed, don't say "wrong code".
            raise DependencyUnavailable("hash_invalid") from None

    def issue_grant(self) -> tuple[str, int]:
        """A signed, short-lived grant to start one session. Returns (cookie value, max-age)."""
        key = self._secrets.cookie_signing_key()
        expires = self._clock() + GRANT_LIFETIME
        value = cookies.sign(
            key, cookies.GRANT, secrets.token_urlsafe(16), int(expires.timestamp())
        )
        return value, int(GRANT_LIFETIME.total_seconds())

    def start_permitted(self, grant_cookie: str | None, session_cookie: str | None) -> bool:
        """May this browser start a new canvas? True for a valid grant or an existing session.

        Reveals nothing but a boolean. Without any cookie no secret lookup happens.
        """
        if not grant_cookie and not session_cookie:
            return False
        key = self._secrets.cookie_signing_key()
        if grant_cookie:
            try:
                cookies.verify(key, grant_cookie, cookies.GRANT, int(self._clock().timestamp()))
                return True
            except AccessRejected:
                pass
        if session_cookie:
            try:
                self.load_session(session_cookie)
                return True
            except AccessRejected:
                pass
        return False

    def start_session(
        self, grant_cookie: str | None, session_cookie: str | None = None
    ) -> tuple[str, Session]:
        """Create a NEW, independent session record. Never resumes or overwrites one.

        Authority is either a grant (consumed once) or an already-authorised session.
        A presented grant must be valid and unused; it is never silently ignored.
        """
        if not grant_cookie and not session_cookie:
            raise AccessRejected("missing")  # no secret lookup for requests with nothing to verify
        key = self._secrets.cookie_signing_key()
        now = self._clock()
        if grant_cookie:
            grant_id, grant_expiry = cookies.verify(
                key, grant_cookie, cookies.GRANT, int(now.timestamp())
            )
            if not self._store.claim_grant(grant_id, datetime.fromtimestamp(grant_expiry, UTC)):
                raise AccessRejected("grant_already_used")
        else:
            self.load_session(session_cookie)  # an existing, authorised session

        session = Session(
            session_id=secrets.token_urlsafe(32),  # 256-bit, server-generated, never client-chosen
            created_at=now,
            expires_at=now + SESSION_LIFETIME,
        )
        self._store.create(session)
        value = cookies.sign(
            key, cookies.SESSION, session.session_id, int(session.expires_at.timestamp())
        )
        return value, session

    def load_session(self, session_cookie: str | None) -> Session:
        """The single authorisation check: signature, purpose, expiry, then server-side record."""
        if not session_cookie:
            raise AccessRejected("missing")  # no secret lookup for requests with nothing to verify
        key = self._secrets.cookie_signing_key()
        now = self._clock()
        session_id, _ = cookies.verify(key, session_cookie, cookies.SESSION, int(now.timestamp()))
        session = self._store.get(session_id)
        if session is None:
            raise AccessRejected("unknown_session")
        if session.expires_at <= now:  # the record, not the cookie, is authoritative
            raise AccessRejected("expired_session")
        return session

    def end_session(self, session_id: str) -> None:
        self._store.delete(session_id)

    @staticmethod
    def seconds_remaining(session: Session, now: datetime) -> int:
        return max(0, int((session.expires_at - now).total_seconds()))

    def now(self) -> datetime:
        return self._clock()
