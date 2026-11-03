from app.access import cookies
from tests.access.conftest import (
    GRANT_COOKIE,
    KEY,
    SESSION_COOKIE,
    csrf,
    enter,
    signed_in,
    start,
)


def state(client):
    return client.get("/api/access-state")


def test_access_state_is_a_bare_boolean_for_each_situation(world):
    anonymous = world.client()
    assert state(anonymous).json() == {"start_permitted": False}

    with_grant = world.client()
    enter(with_grant)
    assert state(with_grant).json() == {"start_permitted": True}  # valid grant

    assert state(signed_in(world)).json() == {"start_permitted": True}  # existing session

    for r in (state(anonymous), state(with_grant)):
        assert set(r.json()) == {"start_permitted"}  # nothing else is exposed
        assert r.headers["cache-control"] == "no-store"


def test_access_state_is_false_for_bad_expired_or_wrong_purpose_cookies(world):
    client = world.client()
    enter(client)
    grant = client.cookies.get(GRANT_COOKIE)
    start(client)
    session = client.cookies.get(SESSION_COOKIE)

    for name, value in (
        (GRANT_COOKIE, "garbage"),
        (GRANT_COOKIE, session),  # a session presented as a grant
        (SESSION_COOKIE, grant),  # a grant presented as a session
        (SESSION_COOKIE, "tampered.value"),
    ):
        client.cookies.clear()
        client.cookies.set(name, value)
        assert state(client).json() == {"start_permitted": False}, name

    world.clock.advance(minutes=11)  # grant lifetime is 10 minutes
    fresh = world.client()
    fresh.cookies.set(GRANT_COOKIE, grant)
    assert state(fresh).json() == {"start_permitted": False}

    expired = signed_in(world)
    world.clock.advance(hours=24, seconds=1)
    assert state(expired).json() == {"start_permitted": False}


def test_access_state_for_a_session_that_no_longer_exists_is_false(world):
    client = signed_in(world)
    world.store.sessions.clear()
    assert state(client).json() == {"start_permitted": False}


def test_access_state_without_cookies_never_touches_secrets_or_store(world):
    world.secrets.fail = True
    world.store.fail = True
    assert state(world.client()).json() == {"start_permitted": False}


def test_access_state_fails_closed_when_it_cannot_verify_a_cookie(world):
    client = world.client()
    enter(client)
    world.secrets.fail = True
    assert state(client).status_code == 503  # never "permitted" on a lookup failure


def test_session_creation_returns_the_server_chosen_navigation_outcome(world):
    client = world.client()
    enter(client)
    response = start(client)
    assert response.status_code == 201
    assert response.json()["next"] == "/conversation"
    assert set(response.json()) == {"created_at", "expires_at", "next"}  # no cookie/secret data


def test_an_existing_session_can_start_another_independent_canvas(world):
    client = signed_in(world)  # no grant left: it was consumed
    assert client.cookies.get(GRANT_COOKIE) is None
    from tests.access.conftest import cookie_payload

    first = cookie_payload(client.cookies.get(SESSION_COOKIE))["i"]
    first_record = world.store.sessions[first]

    assert start(client).status_code == 201
    second = cookie_payload(client.cookies.get(SESSION_COOKIE))["i"]
    assert second != first and len(world.store.sessions) == 2
    assert world.store.sessions[first] == first_record  # the earlier canvas is untouched


def test_a_forged_or_foreign_cookie_cannot_authorise_a_new_canvas(world):
    client = world.client()
    forged = cookies.sign(
        KEY, cookies.SESSION, "never-created", int(world.clock().timestamp()) + 99
    )
    client.cookies.set(SESSION_COOKIE, forged)  # genuinely signed, but no such session exists
    assert start(client).status_code == 401
    assert world.store.sessions == {}
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, "garbage")
    assert start(client).status_code == 401


def test_a_presented_but_used_grant_is_not_silently_replaced_by_the_session(world):
    client = world.client()
    enter(client)
    used = client.cookies.get(GRANT_COOKIE)
    assert start(client).status_code == 201  # consumes the grant, now holds a session
    client.cookies.set(GRANT_COOKIE, used)  # replay the used grant alongside a valid session
    assert start(client).status_code == 401  # rejected, not quietly accepted via the session
    assert len(world.store.sessions) == 1


def test_start_still_requires_csrf_and_same_origin(world):
    client = signed_in(world)
    assert (
        client.post(
            "/api/sessions", headers={**csrf(client), "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.post("/api/sessions").status_code == 403  # no token
    assert len(world.store.sessions) == 1
