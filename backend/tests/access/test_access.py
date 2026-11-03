from app.access.errors import DependencyUnavailable
from tests.access.conftest import (
    CODE,
    GRANT_COOKIE,
    NEW_CODE,
    SESSION_COOKIE,
    enter,
    hash_code,
    set_cookies,
    signed_in,
    start,
)


def test_valid_code_is_accepted_and_issues_a_grant(world):
    client = world.client()
    response = enter(client)
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert any(c.startswith(f"{GRANT_COOKIE}=") for c in set_cookies(response))
    assert world.store.sessions == {}  # a grant is not a session


def test_invalid_code_creates_neither_grant_nor_session(world):
    client = world.client()
    response = enter(client, "wrong-code")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_access_code"
    assert not any(GRANT_COOKIE in c for c in set_cookies(response))
    assert client.cookies.get(GRANT_COOKIE) is None
    assert world.store.sessions == {}
    # ...and without a grant a session cannot be started.
    assert start(client).status_code == 401
    assert world.store.sessions == {}


def test_overlong_code_is_rejected_without_hashing_it(world):
    before = world.secrets.hash_lookups
    response = enter(world.client(), "x" * 5000)
    assert response.status_code == 422
    assert world.secrets.hash_lookups == before


def test_secret_lookup_failure_fails_closed_with_retry_guidance(world):
    world.secrets.fail = True
    client = world.client()
    response = enter(client, CODE)  # even the correct code must not be accepted
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "service_unavailable"
    assert "try again" in response.json()["error"]["message"].lower()
    assert client.cookies.get(GRANT_COOKIE) is None


def test_malformed_stored_hash_fails_closed_not_as_wrong_code(world):
    world.secrets.hash = "not-an-argon2-hash"
    response = enter(world.client(), CODE)
    assert response.status_code == 503


def test_rotation_takes_effect_on_the_next_attempt_without_restart(world):
    client = world.client()
    assert enter(client, CODE).status_code == 200
    assert enter(client, NEW_CODE).status_code == 401

    world.secrets.hash = hash_code(NEW_CODE)  # operator rotates the secret; app keeps running

    assert enter(client, CODE).status_code == 401  # old code now fails
    assert enter(client, NEW_CODE).status_code == 200  # new code succeeds


def test_rotation_does_not_end_existing_sessions(world):
    client = signed_in(world)
    world.secrets.hash = hash_code(NEW_CODE)
    assert client.get("/api/session").status_code == 200  # still valid until its own expiry


def test_session_store_failure_fails_closed_without_an_untracked_session(world):
    client = world.client()
    enter(client)
    world.store.fail = True
    response = start(client)
    assert response.status_code == 503
    assert client.cookies.get(SESSION_COOKIE) is None
    world.store.fail = False
    assert world.store.sessions == {}


def test_signing_key_lookup_failure_blocks_session_start(world):
    client = world.client()
    enter(client)
    world.secrets.fail = True
    assert start(client).status_code == 503
    assert client.cookies.get(SESSION_COOKIE) is None


def test_dependency_unavailable_is_a_known_type():
    # Guards the contract the routes rely on for fail-closed handling.
    assert DependencyUnavailable("x").category == "x"


def test_requests_with_no_cookie_are_401_without_touching_secrets_or_store(world):
    world.secrets.fail = True  # a lookup would raise 503
    world.store.fail = True
    anonymous = world.client()
    assert anonymous.get("/api/session").status_code == 401
    assert start(anonymous).status_code == 401
