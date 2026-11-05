"""The in-memory and DynamoDB stores must behave the same: one set of checks, run against both."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.access.errors import DependencyUnavailable
from app.conversation.dynamo_store import DynamoConversationStore
from app.conversation.store import MAX_TURNS, InMemoryConversationStore
from app.delivery import InMemoryDeliveryStore
from app.delivery.dynamo_store import DynamoDeliveryStore
from app.interview.dynamo_store import DynamoInterviewStore
from app.interview.engine import InterviewState, Target
from app.interview.store import InMemoryInterviewStore

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
END = NOW + timedelta(hours=24)


@pytest.fixture(params=["memory", "dynamodb"])
def kind(request):
    return request.param


@pytest.fixture
def conv(kind, request):
    if kind == "memory":
        return InMemoryConversationStore()
    client, table = request.getfixturevalue("dynamodb")
    return DynamoConversationStore(client, table)


@pytest.fixture
def interview(kind, request):
    if kind == "memory":
        return InMemoryInterviewStore()
    client, table = request.getfixturevalue("dynamodb")
    return DynamoInterviewStore(client, table)


@pytest.fixture
def deliveries(kind, request):
    if kind == "memory":
        return InMemoryDeliveryStore(clock=lambda: NOW)  # the test dates are fixed, not "today"
    client, table = request.getfixturevalue("dynamodb")
    return DynamoDeliveryStore(client, table)


def say(store, sid="s1", text="hello", sub=None, revision=None, at=NOW):
    revision = store.history(sid)[0] if revision is None else revision
    return store.submit(sid, sub or uuid.uuid4().hex, text, revision, at + timedelta(hours=24), at)


def finish(store, result, sid="s1", text="reply"):
    return store.complete_turn(sid, result.assistant_turn.turn_id, text)


# --- conversation ---------------------------------------------------------------------------------


def test_an_answer_is_recorded_with_a_pending_reply_and_moves_the_revision(conv):
    assert conv.history("s1") == (0, [])
    first = say(conv, text="My idea")
    assert first.outcome == "created" and first.revision == 1
    assert (first.user_turn.role, first.user_turn.text, first.user_turn.status) == (
        "user",
        "My idea",
        "complete",
    )
    assert (first.assistant_turn.role, first.assistant_turn.status) == ("assistant", "pending")
    revision, turns = conv.history("s1")
    assert revision == 1 and [t.turn_id for t in turns] == [
        first.user_turn.turn_id,
        first.assistant_turn.turn_id,
    ]


def test_a_retried_answer_is_recorded_once_and_returns_the_same_turns(conv):
    first = say(conv, sub="sub-1")
    again = conv.submit("s1", "sub-1", "hello", 0, END, NOW)
    assert again.outcome == "replayed" and again.revision == 1
    assert again.user_turn == first.user_turn and again.assistant_turn == first.assistant_turn
    assert len(conv.history("s1")[1]) == 2


def test_a_stale_revision_is_refused_and_nothing_is_written(conv):
    finish(conv, say(conv))
    stale = conv.submit("s1", uuid.uuid4().hex, "late", 0, END, NOW)
    assert stale.outcome == "revision_conflict" and stale.revision == 1
    assert len(conv.history("s1")[1]) == 2


def test_a_new_answer_waits_for_the_reply_to_finish(conv):
    first = say(conv)
    assert say(conv).outcome == "unfinished"
    conv.mark("s1", first.assistant_turn.turn_id, "failed")
    assert say(conv).outcome == "unfinished"  # a failed reply still has to be retried first
    finish(conv, first)
    assert say(conv).outcome == "created"


def test_the_conversation_is_bounded(conv):
    for _ in range(MAX_TURNS // 2):
        finish(conv, say(conv))
    assert say(conv).outcome == "full" and len(conv.history("s1")[1]) == MAX_TURNS


def test_a_reply_is_committed_once_and_never_changed_afterwards(conv):
    first = say(conv)
    turn, did = finish(conv, first, text="first reply")
    assert did and turn.status == "complete" and turn.text == "first reply"
    again, did_again = conv.complete_turn("s1", first.assistant_turn.turn_id, "second reply")
    assert not did_again and again.text == "first reply"
    conv.mark("s1", first.assistant_turn.turn_id, "failed")  # too late: it is complete
    assert conv.get_turn("s1", first.assistant_turn.turn_id).status == "complete"
    assert conv.complete_turn("s1", "no-such-turn", "x") == (None, False)


def test_a_failed_reply_can_be_retried_and_completed(conv):
    first = say(conv)
    conv.mark("s1", first.assistant_turn.turn_id, "failed")
    assert conv.get_turn("s1", first.assistant_turn.turn_id).status == "failed"
    conv.mark("s1", first.assistant_turn.turn_id, "pending")
    assert conv.get_turn("s1", first.assistant_turn.turn_id).status == "pending"
    assert finish(conv, first)[1]


def test_turns_are_found_by_id_and_only_within_their_own_session(conv):
    first = say(conv, "s1")
    other = say(conv, "s2")
    assert conv.get_turn("s1", first.user_turn.turn_id) == first.user_turn
    assert conv.get_turn("s2", first.user_turn.turn_id) is None  # another session's id
    assert conv.get_turn("s1", "nope") is None
    assert conv.history("s2")[1][0].turn_id == other.user_turn.turn_id


def test_the_context_for_a_reply_is_the_answer_it_responds_to(conv):
    first = say(conv, text="Idea one")
    finish(conv, first)
    second = say(conv, text="Idea two")
    answered, text, user_ids = conv.context_for("s1", second.assistant_turn.turn_id)
    assert (answered, text) == (second.user_turn.turn_id, "Idea two")
    assert user_ids == (first.user_turn.turn_id, second.user_turn.turn_id)
    assert conv.context_for("s1", "nope") == ("", "", user_ids)


def test_long_text_and_odd_characters_survive(conv):
    text = ("naïve — “quotes” ☕ 日本語\n" * 700)[:19_999]
    first = say(conv, text=text)
    finish(conv, first, text="reply ✓")
    assert conv.history("s1")[1][0].text == text and conv.history("s1")[1][1].text == "reply ✓"


def test_deleting_removes_everything_and_is_repeatable(conv):
    finish(conv, say(conv))
    conv.delete_session("s1")
    conv.delete_session("s1")
    assert conv.history("s1") == (0, [])
    assert say(conv).outcome == "created"  # a clean start is possible


def test_a_purge_only_deletes_the_revision_the_user_saw(conv):
    finish(conv, say(conv))
    assert conv.delete_session_if_revision("s1", 0) is False  # an answer has arrived since
    assert conv.history("s1")[0] == 1 and len(conv.history("s1")[1]) == 2
    assert conv.delete_session_if_revision("s1", 1) is True
    assert conv.history("s1") == (0, [])
    assert conv.delete_session_if_revision("never-existed", 0) is True


def test_nothing_can_be_written_once_a_session_is_being_deleted(conv, kind):
    if kind == "memory":
        pytest.skip("the in-memory store deletes in one locked step")
    first = say(conv)
    finish(conv, first)
    conv._client.update_item(  # the first half of a purge that was interrupted
        TableName=conv._table,
        Key={"session_id": {"S": "s1"}, "sk": {"S": "CONV"}},
        UpdateExpression="SET purging = :y",
        ExpressionAttributeValues={":y": {"S": "yes"}},
    )
    assert say(conv).outcome == "revision_conflict"
    assert conv.delete_session_if_revision("s1", 1) is True  # asking again finishes the job
    assert conv.history("s1") == (0, [])


def test_expired_sessions_are_listed_by_id_a_page_at_a_time(conv):
    # The in-memory store forgets expired sessions whenever a later one is written, so the live one
    # goes in first and the expired ones after it.
    say(conv, "fresh", at=NOW)
    for i in range(5):
        say(conv, f"s{i}", at=NOW - timedelta(hours=30 - i))  # ends 6h, 5h ... before NOW
    seen, token = [], None
    pages = 0
    while True:
        ids, token = conv.expired_session_ids(NOW, 2, token)
        seen += ids
        pages += 1
        if token is None or pages > 10:
            break
    assert sorted(seen) == [f"s{i}" for i in range(5)] and "fresh" not in seen
    assert conv.expired_session_ids(NOW - timedelta(hours=100), 10) == ([], None)


def test_exactly_24_hours_old_is_not_yet_expired(conv):
    say(conv, "edge", at=NOW)
    assert conv.expired_session_ids(END, 10)[0] == []
    assert conv.expired_session_ids(END + timedelta(seconds=1), 10)[0] == ["edge"]


# --- interview state ---------------------------------------------------------------------------------


def full_state() -> InterviewState:
    return InterviewState(
        canvas={"solutionName": "Shift Notes", "opportunityTypes": ["Find Information"]},
        target=Target("probe", "priority"),
        prompted={"kpis"},
        declined={"businessImpact"},
        confirmed={"data"},
        probed={"priority"},
        unknown={"team"},
        attempts={"team": 2},
        conflicts={"team": "Finance"},
        question="Why did you give this idea the priority “Must-have”?",
        confirmed_revision=3,
    )


def test_a_blank_state_is_returned_for_a_session_with_nothing_stored(interview):
    assert interview.get("s1") == InterviewState()


def test_every_part_of_the_state_round_trips(interview):
    interview.put("s1", full_state(), END, NOW)
    assert interview.get("s1") == full_state()
    assert interview.confirmed_revision("s1") == 3


def test_the_state_returned_is_a_copy(interview):
    interview.put("s1", full_state(), END, NOW)
    interview.get("s1").canvas["solutionName"] = "changed"
    assert interview.get("s1").canvas["solutionName"] == "Shift Notes"


def test_confirming_records_only_the_revision_and_keeps_the_draft(interview):
    interview.put("s1", InterviewState(canvas={"team": "Ops"}), END, NOW)
    interview.confirm("s1", 4, END, NOW)
    assert interview.confirmed_revision("s1") == 4 and interview.get("s1").canvas == {"team": "Ops"}


def test_the_interview_views_agree_between_stores(interview):
    interview.put("s1", full_state(), END, NOW)
    assert interview.canvas_view("s1")["fields"]["solutionName"] == "Shift Notes"
    assert interview.progression_view("s1")["phase"] in {
        "interview",
        "clarification",
        "data_and_systems",
    }
    canvas, declined, ready, confirmed = interview.snapshot("s1")
    assert (
        canvas["solutionName"] == "Shift Notes"
        and declined == ("businessImpact",)
        and confirmed == 3
    )
    assert ready is False


def test_each_session_has_its_own_state_and_deleting_one_leaves_the_other(interview):
    interview.put("s1", full_state(), END, NOW)
    interview.put("s2", InterviewState(canvas={"team": "B"}), END, NOW)
    interview.delete_session("s1")
    interview.delete_session("s1")
    assert interview.get("s1") == InterviewState() and interview.get("s2").canvas == {"team": "B"}


# --- delivery records ----------------------------------------------------------------------------------


def test_a_delivery_is_recorded_per_document_and_session(deliveries):
    assert deliveries.record("s1", "canvas", 3, NOW, END)
    assert deliveries.record("s1", "appendix", 3, NOW, END)
    got = deliveries.get("s1")
    assert {k: v.revision for k, v in got.items()} == {"canvas": 3, "appendix": 3}
    assert got["canvas"].completed_at == NOW and got["canvas"].kind == "canvas"
    assert deliveries.get("s2") == {}


def test_a_repeat_is_harmless_and_an_older_revision_never_replaces_a_newer_one(deliveries):
    deliveries.record("s1", "canvas", 3, NOW, END)
    assert deliveries.record("s1", "canvas", 3, NOW + timedelta(minutes=5), END)
    assert deliveries.get("s1")["canvas"].completed_at == NOW  # the first time is kept
    assert deliveries.record("s1", "canvas", 5, NOW, END)
    assert deliveries.record("s1", "canvas", 4, NOW, END) is False
    assert deliveries.get("s1")["canvas"].revision == 5


def test_deleting_the_records_removes_both_and_is_repeatable(deliveries):
    deliveries.record("s1", "canvas", 3, NOW, END)
    deliveries.record("s1", "appendix", 3, NOW, END)
    deliveries.delete_session("s1")
    deliveries.delete_session("s1")
    assert deliveries.get("s1") == {}


# --- failure is closed, and says nothing --------------------------------------------------------------


def test_a_corrupt_interview_record_fails_closed_instead_of_starting_blank(dynamodb):
    client, table = dynamodb
    client.put_item(
        TableName=table,
        Item={"session_id": {"S": "s1"}, "sk": {"S": "INTERVIEW"}, "state": {"S": "{not json"}},
    )
    with pytest.raises(DependencyUnavailable):
        DynamoInterviewStore(client, table).get("s1")


def test_a_missing_table_fails_closed_without_provider_detail(monkeypatch):
    from moto import mock_aws

    from app import dynamo

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        client = dynamo.build_client("ap-southeast-2")
        for call in (
            lambda: DynamoConversationStore(client, "nope").history("s1"),
            lambda: DynamoDeliveryStore(client, "nope").get("s1"),
            lambda: DynamoInterviewStore(client, "nope").get("s1"),
        ):
            with pytest.raises(DependencyUnavailable) as caught:
                call()
            assert "nope" not in repr(caught.value) and "ResourceNotFound" not in repr(caught.value)


def test_a_bad_page_token_is_refused(dynamodb):
    client, table = dynamodb
    store = DynamoConversationStore(client, table)
    for token in ("not json", '"a string"', '{"session_id": {"S": "x"}}'):
        with pytest.raises(DependencyUnavailable):
            store.expired_session_ids(NOW, 5, token)
