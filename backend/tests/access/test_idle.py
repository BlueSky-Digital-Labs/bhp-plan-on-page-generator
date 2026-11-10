"""A session with no activity for 15 minutes ends and its data is deleted (24 hours is the other limit)."""

from datetime import timedelta

import pytest

from app.access.store import SESSION_IDLE
from app.expiry import run_expiry
from tests.access.conftest import csrf, signed_in
from tests.interview.conftest import browser  # noqa: F401
from tests.interview.test_interview_api import say, wire

IDLE = SESSION_IDLE


def _stay(client):
    return client.post("/api/session/keepalive", headers=csrf(client))


def test_the_idle_limit_is_fifteen_minutes():
    assert IDLE == timedelta(minutes=15)


def test_a_session_is_alive_just_before_fifteen_idle_minutes_and_over_at_fifteen(world):
    client = signed_in(world)
    world.clock.advance(minutes=14, seconds=59)
    assert client.get("/api/session").status_code == 200
    world.clock.advance(seconds=1)
    assert client.get("/api/session").status_code == 401  # ended for want of activity


def test_the_remaining_idle_time_is_reported_and_never_beyond_the_24_hour_end(world):
    client = signed_in(world)
    assert client.get("/api/session").json()["idle_seconds_remaining"] == 15 * 60
    world.clock.advance(minutes=5)
    assert client.get("/api/session").json()["idle_seconds_remaining"] == 10 * 60


def test_looking_at_the_page_does_not_count_as_activity(world):
    wire(world)
    client = browser(world)
    for _ in range(14):  # a tab left open, polling every minute
        world.clock.advance(minutes=1)
        assert client.get("/api/conversation").status_code == 200
        assert client.get("/api/session").status_code == 200
        assert client.get("/api/downloads").status_code in (200, 409)
    world.clock.advance(minutes=1)
    assert client.get("/api/session").status_code == 401


def test_staying_signed_in_restarts_the_idle_clock_but_not_the_24_hour_end(world):
    client = signed_in(world)
    record = world.store.sessions[next(iter(world.store.sessions))]
    for _ in range(5):
        world.clock.advance(minutes=10)
        assert _stay(client).status_code == 200
    body = client.get("/api/session").json()
    assert body["idle_seconds_remaining"] == 15 * 60
    assert body["expires_at"] == record.expires_at.isoformat()


def test_sending_an_answer_restarts_the_idle_clock(world):
    wire(world)
    client = browser(world)
    world.clock.advance(minutes=10)
    say(client, "We copy shift notes between spreadsheets.")
    world.clock.advance(minutes=10)  # 20 minutes since the start, 10 since the answer
    assert client.get("/api/session").status_code == 200
    world.clock.advance(minutes=5)
    assert client.get("/api/session").status_code == 401


def test_an_idle_session_loses_its_data_at_once_when_it_is_next_seen(world):
    wire(world)
    client = browser(world)
    say(client, "We copy shift notes between spreadsheets.")
    store = world.conversation._store
    sid = next(iter(world.store.sessions))
    assert store.history(sid)[1] != []
    world.clock.advance(minutes=16)
    assert client.get("/api/conversation").status_code == 401
    assert store.history(sid) == (0, [])  # the conversation went with the session
    assert sid not in world.store.sessions


def test_the_scheduled_sweep_removes_an_idle_session_nobody_comes_back_to(world):
    wire(world)
    client = browser(world)
    say(client, "We copy shift notes between spreadsheets.")
    store = world.conversation._store
    sid = next(iter(world.store.sessions))

    class Access:
        ended: list[str] = []

        def end_session(self, session_id):
            self.ended.append(session_id)
            world.store.sessions.pop(session_id, None)

    access = Access()
    just_before = world.clock() + IDLE - timedelta(seconds=1)
    assert run_expiry(world.conversation, access, now=just_before).found == 0
    result = run_expiry(world.conversation, access, now=world.clock() + IDLE + timedelta(seconds=1))
    assert (result.found, result.deleted) == (1, 1)
    assert store.history(sid) == (0, []) and access.ended == [sid]


def test_activity_pushes_the_sweep_deadline_back_too(world):
    wire(world)
    client = browser(world)
    say(client, "We copy shift notes between spreadsheets.")
    world.clock.advance(minutes=10)
    assert _stay(client).status_code == 200
    assert (
        run_expiry(
            world.conversation,
            type("A", (), {"end_session": lambda *a: None})(),
            now=world.clock() + timedelta(minutes=14),
        ).found
        == 0
    )
    assert (
        run_expiry(
            world.conversation,
            type("A", (), {"end_session": lambda *a: None})(),
            now=world.clock() + timedelta(minutes=16),
        ).found
        == 1
    )


@pytest.mark.parametrize("route", ["/api/session/keepalive"])
def test_staying_signed_in_needs_a_session_and_the_csrf_token(world, route):
    anonymous = world.client()
    assert anonymous.post(route, headers=csrf(anonymous)).status_code == 401
    client = signed_in(world)
    assert client.post(route).status_code == 403  # no CSRF token
