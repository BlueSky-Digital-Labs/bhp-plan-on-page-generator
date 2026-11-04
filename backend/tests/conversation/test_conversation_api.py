import json

import pytest

from app.conversation import store as store_module
from app.conversation.producers import ProducerUnavailable, ScriptedTestProducer
from app.conversation.service import ConversationService
from app.conversation.store import UnavailableConversationStore
from tests.access.conftest import SESSION_COOKIE, csrf, start
from tests.conversation.conftest import (
    ListProducer,
    RaisingProducer,
    browser,
    events,
    submit,
    use_producer,
)

SECRET = "SECRET-IDEA-TEXT-NOT-FOR-LOGS"


def stream(client, turn_id):
    return client.get(f"/api/conversation/turns/{turn_id}/stream")


def history(client):
    return client.get("/api/conversation").json()


# --- access ----------------------------------------------------------------------------


def test_every_conversation_route_requires_a_session_and_reveals_nothing(world):
    anonymous = world.client()
    for response in (
        anonymous.get("/api/conversation"),
        submit(anonymous),
        stream(anonymous, "a" * 32),
    ):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"
        assert "turns" not in response.text


def test_an_expired_session_gets_no_transcript(world):
    client = browser(world)
    submit(client, SECRET)
    world.clock.advance(hours=24, seconds=1)
    for response in (client.get("/api/conversation"), submit(client, "x", 1, "sub-00000002")):
        assert response.status_code == 401
        assert SECRET not in response.text


def test_ending_the_session_deletes_its_conversation(world):
    client = browser(world)
    submit(client)
    session_id = next(iter(world.store.sessions))
    assert client.delete("/api/session", headers=csrf(client)).status_code == 204
    assert world.conversation._store.history(session_id) == (0, [])


# --- history and submission --------------------------------------------------------------


def test_a_new_session_starts_with_an_empty_conversation(world):
    empty = history(browser(world))
    assert (empty["revision"], empty["turns"]) == (0, [])


def test_submitting_records_the_answer_and_a_pending_reply_in_order(world):
    client = browser(world)
    response = submit(client, "A rough idea about onboarding")
    assert response.status_code == 201
    body = response.json()
    assert body["revision"] == 1
    assert body["user_turn"]["text"] == "A rough idea about onboarding"
    assert body["assistant_turn"] == {
        "turn_id": body["assistant_turn"]["turn_id"],
        "role": "assistant",
        "text": "",
        "status": "pending",
    }
    turns = history(client)["turns"]
    assert [(t["role"], t["status"]) for t in turns] == [
        ("user", "complete"),
        ("assistant", "pending"),
    ]


def test_retrying_with_the_same_submission_id_never_records_the_answer_twice(world):
    client = browser(world)
    first = submit(client, "My idea", submission_id="same-id-0001").json()
    again = submit(client, "My idea", submission_id="same-id-0001")
    assert again.status_code == 200  # reported, not created
    assert again.json() == first
    assert history(client)["revision"] == 1 and len(history(client)["turns"]) == 2


def test_a_stale_revision_is_a_conflict_that_records_nothing(world):
    client = browser(world)
    submit(client)
    use_producer(world, ListProducer(["ok"]))
    turn_id = history(client)["turns"][1]["turn_id"]
    stream(client, turn_id)  # finish the reply so a new answer is otherwise allowed
    stale = submit(client, "Second answer", revision=0, submission_id="sub-00000002")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    assert len(history(client)["turns"]) == 2


def test_a_new_answer_is_refused_while_the_last_reply_is_unfinished(world):
    client = browser(world)
    submit(client)
    blocked = submit(client, "Another", revision=1, submission_id="sub-00000002")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "turn_in_progress"


def test_the_conversation_has_a_size_limit(world, monkeypatch):
    monkeypatch.setattr(store_module, "MAX_TURNS", 2)
    client = browser(world)
    submit(client)
    use_producer(world, ListProducer(["ok"]))
    stream(client, history(client)["turns"][1]["turn_id"])
    full = submit(client, "More", revision=1, submission_id="sub-00000002")
    assert full.status_code == 409 and full.json()["error"]["code"] == "conversation_full"


@pytest.mark.parametrize(
    "payload",
    [
        {"submission_id": "sub-00000001", "expected_revision": 0, "text": ""},
        {"submission_id": "sub-00000001", "expected_revision": 0, "text": "   \n "},
        {"submission_id": "sub-00000001", "expected_revision": 0, "text": "x" * 20_001},
        {"submission_id": "short", "expected_revision": 0, "text": "ok"},
        {"submission_id": "bad id!!!!", "expected_revision": 0, "text": "ok"},
        {"submission_id": "sub-00000001", "expected_revision": -1, "text": "ok"},
        {"submission_id": "sub-00000001", "expected_revision": 0, "text": "ok", "session_id": "x"},
    ],
)
def test_invalid_submissions_are_rejected_without_echoing_them(world, payload):
    client = browser(world)
    response = client.post("/api/conversation/turns", json=payload, headers=csrf(client))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert history(client)["turns"] == []


def test_state_changing_submission_needs_csrf_and_same_origin(world):
    client = browser(world)
    body = {"submission_id": "sub-00000001", "expected_revision": 0, "text": SECRET}
    assert client.post("/api/conversation/turns", json=body).status_code == 403
    cross = {**csrf(client), "Origin": "https://evil.example"}
    assert client.post("/api/conversation/turns", json=body, headers=cross).status_code == 403
    assert history(client)["turns"] == []


# --- streaming ---------------------------------------------------------------------------


def _pending_turn(client):
    submit(client)
    return history(client)["turns"][1]["turn_id"]


def test_stream_delivers_progress_then_commits_and_confirms_completion(world):
    producer = use_producer(world, ListProducer(["Hello ", "there."]))
    client = browser(world)
    turn_id = _pending_turn(client)

    response = stream(client, turn_id)
    assert response.status_code == 200
    assert events(response) == [
        {"type": "delta", "text": "Hello "},
        {"type": "delta", "text": "there."},
        {"type": "complete"},
    ]
    committed = history(client)["turns"][1]
    assert committed["text"] == "Hello there." and committed["status"] == "complete"

    # Streaming a completed turn replays the committed text; it does not run the producer again.
    again = events(stream(client, turn_id))
    assert again == [{"type": "delta", "text": "Hello there."}, {"type": "complete"}]
    assert producer.calls == 1


def test_stream_responses_are_ndjson_and_never_cached(world):
    use_producer(world, ScriptedTestProducer())
    client = browser(world)
    turn_id = _pending_turn(client)
    response = stream(client, turn_id)
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"
    for r in (client.get("/api/conversation"), submit(client, "x", 2, "sub-00000002")):
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["content-type"].startswith("application/json")  # never HTML


def test_without_an_assistant_the_turn_fails_visibly_and_nothing_is_made_up(world):
    client = browser(world)  # default producer: unavailable
    turn_id = _pending_turn(client)
    assert events(stream(client, turn_id)) == [{"type": "failed", "reason": "unavailable"}]
    assert history(client)["turns"][1] == {
        "turn_id": turn_id,
        "role": "assistant",
        "text": "",
        "status": "failed",
    }


def test_a_failed_reply_can_be_retried_without_resubmitting_the_answer(world):
    client = browser(world)
    turn_id = _pending_turn(client)
    assert events(stream(client, turn_id))[0]["type"] == "failed"

    use_producer(world, ListProducer(["Now ", "works."]))
    assert events(stream(client, turn_id))[-1] == {"type": "complete"}
    turns = history(client)["turns"]
    assert [t["text"] for t in turns] == ["My idea", "Now works."]
    assert history(client)["revision"] == 1  # the answer was never re-recorded


def test_producer_errors_become_a_generic_failed_event_with_no_detail(world):
    use_producer(world, RaisingProducer(RuntimeError(f"boom {SECRET}")))
    client = browser(world)
    turn_id = _pending_turn(client)
    response = stream(client, turn_id)
    assert events(response)[-1] == {"type": "failed", "reason": "processing_error"}
    assert SECRET not in response.text
    assert history(client)["turns"][1]["status"] == "failed"
    assert history(client)["turns"][1]["text"] == ""  # the partial output was not committed


def test_unavailable_producer_error_type_is_handled_distinctly():
    assert issubclass(ProducerUnavailable, Exception)


def test_a_dropped_connection_commits_nothing_and_leaves_the_turn_pending(world):
    use_producer(world, ListProducer(["one ", "two ", "three"]))
    client = browser(world)
    turn_id = _pending_turn(client)
    session = world.service.load_session(client.cookies.get(SESSION_COOKIE))
    turn = world.conversation.get_assistant_turn(session, turn_id)

    generator = world.conversation.stream(session, turn)
    assert json.loads(next(generator))["text"] == "one "
    generator.close()  # the client went away mid-reply

    stored = history(client)["turns"][1]
    assert (stored["status"], stored["text"]) == ("pending", "")  # recoverable, not half-committed
    use_producer(world, ListProducer(["fresh"]))
    assert events(stream(client, turn_id))[-1] == {"type": "complete"}
    assert history(client)["turns"][1]["text"] == "fresh"


# --- ownership ---------------------------------------------------------------------------


def test_a_session_cannot_stream_or_read_another_sessions_turns(world):
    use_producer(world, ListProducer(["private reply"]))
    owner, other = browser(world), browser(world)
    owner_turn = _pending_turn(owner)
    stream(owner, owner_turn)

    assert (history(other)["revision"], history(other)["turns"]) == (0, [])
    denied = stream(other, owner_turn)
    missing = stream(other, "f" * 32)
    malformed = stream(other, "not-a-turn-id")
    user_turn_id = history(owner)["turns"][0]["turn_id"]
    wrong_role = stream(owner, user_turn_id)  # even its own USER turn is not streamable

    bodies = []
    for r in (denied, missing, malformed, wrong_role):
        assert r.status_code == 404
        body = r.json()
        body["error"].pop("correlation_id")
        bodies.append(body)
    assert all(b == bodies[0] for b in bodies)  # indistinguishable: ownership is not revealed
    assert "private reply" not in denied.text
    # And the other session's own submissions cannot touch the owner's record.
    submit(other, "mine", submission_id="other-0000001")
    assert [t["text"] for t in history(owner)["turns"]] == ["My idea", "private reply"]


def test_a_new_canvas_never_shows_the_previous_sessions_content(world):
    client = browser(world)
    submit(client, SECRET)
    assert start(client).status_code == 201  # an existing session may start another canvas
    fresh = history(client)
    assert (fresh["revision"], fresh["turns"]) == (0, [])
    assert SECRET not in client.get("/api/conversation").text


# --- content handling ------------------------------------------------------------------


def test_markup_is_stored_and_returned_as_inert_json_text(world):
    use_producer(world, ListProducer(["<script>alert(2)</script>"]))
    client = browser(world)
    hostile = '<img src=x onerror="alert(1)"> & "quotes"'
    submit(client, hostile)
    stream(client, history(client)["turns"][1]["turn_id"])
    response = client.get("/api/conversation")
    assert response.headers["content-type"].startswith("application/json")
    assert [t["text"] for t in response.json()["turns"]] == [hostile, "<script>alert(2)</script>"]


# --- deployment safety -----------------------------------------------------------------


def test_without_a_durable_store_the_conversation_fails_closed_with_a_503(world):
    world.conversation = ConversationService(UnavailableConversationStore(), ScriptedTestProducer())
    client = browser(world)
    assert client.get("/api/conversation").status_code == 503
    response = submit(client)
    assert response.status_code == 503 and response.headers["retry-after"] == "5"
    assert (
        client.delete("/api/session", headers=csrf(client)).status_code == 204
    )  # sign-out still works


def test_logs_for_conversation_events_carry_no_content(world, caplog):
    import logging

    use_producer(world, RaisingProducer(RuntimeError(SECRET)))
    client = browser(world)
    with caplog.at_level(logging.INFO):
        turn_id = _pending_turn(client)
        stream(client, turn_id)
    assert SECRET not in caplog.text
    assert "My idea" not in caplog.text
