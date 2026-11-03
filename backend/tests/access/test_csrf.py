import pytest

from tests.access.conftest import CODE, GRANT_COOKIE, ORIGIN, csrf, enter, signed_in, start


def _post_access(client, headers=None):
    return client.post("/api/access", data={"access_code": CODE}, headers=headers or {})


def test_missing_or_wrong_csrf_token_is_rejected(world):
    client = world.client()
    token = client.get("/api/csrf").json()["csrf_token"]
    for headers in ({}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": token + "x"}):
        response = _post_access(client, headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "request_not_verified"
    assert client.cookies.get(GRANT_COOKIE) is None


def test_token_header_without_the_matching_cookie_is_rejected(world):
    client = world.client()
    token = client.get("/api/csrf").json()["csrf_token"]
    client.cookies.clear()  # attacker can set a header but cannot set our cookie
    assert _post_access(client, {"X-CSRF-Token": token}).status_code == 403


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://testserver.evil.example", "null"]
)
def test_cross_site_origin_is_rejected_on_every_state_changing_route(world, origin):
    client = signed_in(world)
    headers = {**csrf(client), "Origin": origin}
    sid_count = len(world.store.sessions)

    assert (
        client.post("/api/access", data={"access_code": CODE}, headers=headers).status_code == 403
    )
    assert client.post("/api/sessions", headers=headers).status_code == 403
    assert client.delete("/api/session", headers=headers).status_code == 403
    assert len(world.store.sessions) == sid_count  # nothing was created or deleted


def test_cross_site_session_creation_does_not_consume_the_grant(world):
    client = world.client()
    enter(client)
    forged = {**csrf(client), "Origin": "https://evil.example"}
    assert client.post("/api/sessions", headers=forged).status_code == 403
    assert world.store.sessions == {}
    assert start(client).status_code == 201  # the legitimate request still works


def test_request_proving_no_origin_at_all_is_rejected(world):
    client = world.client()
    headers = csrf(client)
    del client.headers["Origin"]
    assert (
        client.post("/api/access", data={"access_code": CODE}, headers=headers).status_code == 403
    )


def test_sec_fetch_site_same_origin_is_accepted_when_origin_is_absent(world):
    client = world.client()
    headers = {**csrf(client), "Sec-Fetch-Site": "same-origin"}
    del client.headers["Origin"]
    assert (
        client.post("/api/access", data={"access_code": CODE}, headers=headers).status_code == 200
    )
    cross = {**csrf(client), "Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/access", data={"access_code": CODE}, headers=cross).status_code == 403


def test_same_origin_with_matching_token_is_accepted(world):
    client = world.client(Origin=ORIGIN)
    assert enter(client).status_code == 200
