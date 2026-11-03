"""Signed, opaque cookies.

A cookie carries only {purpose, opaque id, expiry}: never the access code or any idea
content. Each purpose is signed with its own key derived from the master key, so a grant
cannot be presented as a session even if the purpose field were edited.
"""

import base64
import hashlib
import hmac
import json

from app.access.errors import AccessRejected

GRANT = "grant"
SESSION = "session"
_MAX_LENGTH = 512


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _purpose_key(master: bytes, purpose: str) -> bytes:
    return hmac.new(master, b"cookie-key:" + purpose.encode(), hashlib.sha256).digest()


def sign(master: bytes, purpose: str, ident: str, expires_at: int) -> str:
    body = json.dumps({"p": purpose, "i": ident, "e": expires_at}, separators=(",", ":"))
    payload = _b64(body.encode())
    signature = hmac.new(_purpose_key(master, purpose), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(signature)}"


def verify(master: bytes, value: str | None, purpose: str, now: int) -> tuple[str, int]:
    """Return (opaque id, expiry epoch) or raise AccessRejected(<safe reason>)."""
    if not value:
        raise AccessRejected("missing")
    if len(value) > _MAX_LENGTH:
        raise AccessRejected("malformed")
    payload, dot, signature = value.partition(".")
    if not dot:
        raise AccessRejected("malformed")
    try:
        claims = json.loads(_unb64(payload))
        claimed_purpose, ident, expires_at = claims["p"], claims["i"], claims["e"]
        presented = _unb64(signature)
    except (ValueError, KeyError, TypeError):
        raise AccessRejected("malformed") from None
    if not (
        isinstance(claimed_purpose, str)
        and isinstance(ident, str)
        and isinstance(expires_at, int)
        and not isinstance(expires_at, bool)
    ):
        raise AccessRejected("malformed")

    # Check the signature under the key of the purpose the cookie *claims*; a relabelled
    # payload therefore fails here, and a genuine cookie of another purpose is told apart.
    expected = hmac.new(
        _purpose_key(master, claimed_purpose), payload.encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, presented):
        raise AccessRejected("bad_signature")
    if claimed_purpose != purpose:
        raise AccessRejected("wrong_purpose")
    if expires_at <= now:
        raise AccessRejected("expired")
    return ident, expires_at
