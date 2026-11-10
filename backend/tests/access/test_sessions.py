from datetime import timedelta

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


def _sid(client) -> str:
    from tests.access.conftest import cookie_payload

    return cookie_payload(client.cookies.get(SESSION_COOKIE))["i"]


def test_two_browsers_get_different_server_generated_ids(world):
    a, b = signed_in(world), signed_in(world)
    ids = {_sid(a), _sid(b)}
    assert len(ids) == 2 and set(world.store.sessions) == ids
    assert all(len(i) >= 40 for i in ids)  # 256 bits of randomness, url-safe


def test_cross_session_reads_and_writes_are_denied(world):
    a, b = signed_in(world), signed_in(world)
    sid_a, sid_b = _sid(a), _sid(b)
    record_b = world.store.sessions[sid_b]

    # A tries to select B's session through every channel a client controls.
    own = a.get("/api/session", params={"session_id": sid_b}, headers={"X-Session-Id": sid_b})
    assert own.status_code == 200
    assert own.json()["created_at"] == world.store.sessions[sid_a].created_at.isoformat()
    assert own.json()["created_at"] != record_b.created_at.isoformat() or sid_a != sid_b
    deleted = a.request(
        "DELETE",
        f"/api/session?session_id={sid_b}",
        json={"session_id": sid_b},
        headers=csrf(a),
    )
    assert deleted.status_code == 204

    # Only A's own record was touched.
    assert sid_a not in world.store.sessions
    assert world.store.sessions[sid_b] == record_b
    assert b.get("/api/session").status_code == 200


def test_substituting_another_sessions_id_into_a_cookie_is_denied(world):
    a, b = signed_in(world), signed_in(world)
    a.cookies.clear()
    a.cookies.set(SESSION_COOKIE, b.cookies.get(SESSION_COOKIE).replace("session", "session"))
    # A cannot mint a valid cookie for B's id without the key.
    from tests.access.test_cookies import _forge

    a.cookies.clear()
    a.cookies.set(SESSION_COOKIE, _forge(b.cookies.get(SESSION_COOKIE), i=_sid(b)[:-1] + "x"))
    assert a.get("/api/session").status_code == 401


def test_session_requires_authentication(world):
    anonymous = world.client()
    assert anonymous.get("/api/session").status_code == 401
    assert anonymous.delete("/api/session", headers=csrf(anonymous)).status_code == 401


def _stay(client):
    """The user does something (the page's "keep my session" button): the idle clock restarts."""
    return client.post("/api/session/keepalive", headers=csrf(client))


def test_session_records_a_fixed_24_hour_expiry_not_extended_by_activity(world):
    client = signed_in(world)
    record = world.store.sessions[_sid(client)]
    assert record.expires_at - record.created_at == timedelta(hours=24)
    first = client.get("/api/session").json()

    for _ in range(12 * 6):  # twelve hours of activity every ten minutes
        world.clock.advance(minutes=10)
        assert _stay(client).status_code == 200
    now = client.get("/api/session").json()
    assert (now["created_at"], now["expires_at"]) == (first["created_at"], first["expires_at"])
    assert world.store.sessions[_sid(client)].expires_at == record.expires_at  # never extended

    for _ in range(12 * 6 - 1):
        world.clock.advance(minutes=10)
        assert _stay(client).status_code == 200
    world.clock.advance(minutes=10, seconds=1)  # past the fixed 24 hours, however active it was
    assert client.get("/api/session").status_code == 401


def test_server_record_expiry_is_authoritative_over_the_cookie(world):
    client = signed_in(world)
    sid = _sid(client)
    # A cookie claiming a far-future expiry, validly signed, for a record that has expired.
    far = int(world.clock().timestamp()) + 10 * 86_400
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, cookies.sign(KEY, cookies.SESSION, sid, far))
    world.clock.advance(hours=24, seconds=1)
    assert client.get("/api/session").status_code == 401


def test_starting_another_canvas_creates_an_independent_record(world):
    client = signed_in(world)
    first_id = _sid(client)
    first = world.store.sessions[first_id]
    world.clock.advance(hours=2)

    assert enter(client).status_code == 200  # a new grant is needed for a new canvas
    assert start(client).status_code == 201
    second_id = _sid(client)

    assert second_id != first_id and len(world.store.sessions) == 2
    # The first record was not overwritten and keeps its ORIGINAL expiry.
    assert world.store.sessions[first_id] == first
    assert world.store.sessions[second_id].created_at > first.created_at
    assert world.store.sessions[second_id].expires_at > first.expires_at


def test_a_grant_can_only_start_one_session(world):
    client = world.client()
    enter(client)
    grant = client.cookies.get(GRANT_COOKIE)
    assert start(client).status_code == 201
    assert len(world.store.sessions) == 1

    client.cookies.set(GRANT_COOKIE, grant)  # replay the consumed grant
    assert start(client).status_code == 401
    assert len(world.store.sessions) == 1


def test_session_start_without_a_grant_is_rejected(world):
    client = world.client()
    assert start(client).status_code == 401
    assert world.store.sessions == {}


def test_ending_a_session_removes_the_record_and_cookie(world):
    client = signed_in(world)
    response = client.delete("/api/session", headers=csrf(client))
    assert response.status_code == 204
    assert world.store.sessions == {}
    assert client.get("/api/session").status_code == 401
