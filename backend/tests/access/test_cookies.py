import base64
import json

from app.access import cookies
from tests.access.conftest import (
    CODE,
    GRANT_COOKIE,
    KEY,
    SESSION_COOKIE,
    cookie_payload,
    enter,
    set_cookies,
    signed_in,
    start,
)


def _header(response, name):
    return next(c for c in set_cookies(response) if c.startswith(f"{name}="))


def _attrs(header: str) -> dict[str, str]:
    parts = [p.strip() for p in header.split(";")]
    return {p.split("=")[0].lower(): (p.split("=", 1)[1] if "=" in p else "") for p in parts[1:]}


def test_grant_and_session_cookies_are_secure_httponly_lax_and_bounded(world):
    client = world.client()
    grant = _attrs(_header(enter(client), GRANT_COOKIE))
    assert {"secure", "httponly"} <= set(grant)
    assert grant["samesite"].lower() == "lax" and grant["path"] == "/" and "domain" not in grant
    assert 0 < int(grant["max-age"]) <= 600

    session_response = start(client)
    session = _attrs(_header(session_response, SESSION_COOKIE))
    assert {"secure", "httponly"} <= set(session)
    assert session["samesite"].lower() == "lax" and session["path"] == "/"
    assert "domain" not in session
    assert 86_000 < int(session["max-age"]) <= 86_400  # bounded to the fixed 24 h
    # The grant is replaced: it is cleared in the same response.
    assert "max-age=0" in _header(session_response, GRANT_COOKIE).lower()


def test_cookie_payload_is_only_purpose_opaque_id_and_expiry(world):
    client = world.client()
    grant_value = _header(enter(client), GRANT_COOKIE).split("=", 1)[1].split(";")[0]
    assert set(cookie_payload(grant_value)) == {"p", "i", "e"}
    assert cookie_payload(grant_value)["p"] == "grant"

    start(client)
    session_value = client.cookies.get(SESSION_COOKIE)
    payload = cookie_payload(session_value)
    assert set(payload) == {"p", "i", "e"} and payload["p"] == "session"
    assert CODE not in session_value and CODE not in grant_value
    # The id in the cookie is exactly the server-side record's id, nothing more.
    assert payload["i"] in world.store.sessions


def _forge(value: str, **changes) -> str:
    payload, sig = value.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims.update(changes)
    raw = json.dumps(claims, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode() + "." + sig


def test_tampered_payload_or_signature_is_rejected(world):
    client = signed_in(world)
    good = client.cookies.get(SESSION_COOKIE)
    other_sid = "someone-elses-session-id"
    for bad in (
        _forge(good, i=other_sid),  # swap in another session id, keep the signature
        _forge(good, p="grant"),  # relabel the purpose
        _forge(good, e=9_999_999_999),  # extend the expiry
        good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB"),  # corrupt the signature
        good + "x",
        "garbage",
        "a.b",
        ".",
        "x" * 600,
    ):
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, bad)
        assert client.get("/api/session").status_code == 401, bad[:20]


def test_wrong_purpose_cookies_are_rejected_both_ways(world):
    client = world.client()
    enter(client)
    grant = client.cookies.get(GRANT_COOKIE)
    assert start(client).status_code == 201
    session = client.cookies.get(SESSION_COOKIE)

    # A grant presented as a session.
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, grant)
    assert client.get("/api/session").status_code == 401

    # A session presented as a grant.
    client.cookies.clear()
    client.cookies.set(GRANT_COOKIE, session)
    from tests.access.conftest import csrf

    assert client.post("/api/sessions", headers=csrf(client)).status_code == 401


def test_expired_cookies_are_rejected(world):
    client = world.client()
    enter(client)
    world.clock.advance(minutes=11)  # grant lifetime is 10 minutes
    assert start(client).status_code == 401

    client = signed_in(world)
    world.clock.advance(hours=24, seconds=1)
    assert client.get("/api/session").status_code == 401


def test_a_validly_signed_cookie_for_a_missing_session_is_rejected_uniformly(world):
    client = signed_in(world)
    expiry = int(world.clock().timestamp()) + 3600
    forged = cookies.sign(KEY, cookies.SESSION, "never-created", expiry)  # genuine signature
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, forged)
    unknown = client.get("/api/session")

    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, "garbage")
    malformed = client.get("/api/session")

    assert unknown.status_code == malformed.status_code == 401
    # Nothing distinguishes "no such session" from "bad cookie" to the caller.
    bodies = [r.json() for r in (unknown, malformed)]
    for body in bodies:
        body["error"].pop("correlation_id")  # unique per request by design
    assert bodies[0] == bodies[1]


def test_deleted_session_is_rejected(world):
    client = signed_in(world)
    world.store.sessions.clear()
    assert client.get("/api/session").status_code == 401
