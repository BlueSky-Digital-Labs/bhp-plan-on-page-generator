"""The scheduled sweep, the sign-in store and the deployed wiring, on DynamoDB (moto)."""

from datetime import UTC, datetime, timedelta

import pytest

from app import dynamo
from app.access.errors import DependencyUnavailable
from app.access.service import AccessService
from app.access.store import DynamoSessionStore, Session
from app.config import AppEnv, Settings
from app.conversation.dynamo_store import DynamoConversationStore
from app.conversation.producers import UnavailableProducer
from app.conversation.service import ConversationService
from app.delivery.dynamo_store import DynamoDeliveryStore
from app.expiry import run_expiry
from app.interview.dynamo_store import DynamoInterviewStore
from app.interview.engine import InterviewState
from app.main import build_conversation

START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
LIFETIME = timedelta(hours=24)


def items_of(client, table, session_id):
    """Every item stored under this session id, found by scanning the whole table."""
    found, token = [], None
    while True:
        request = {"TableName": table, **({"ExclusiveStartKey": token} if token else {})}
        page = client.scan(**request)
        found += [i for i in page["Items"] if i["session_id"]["S"] == session_id]
        token = page.get("LastEvaluatedKey")
        if not token:
            return found


@pytest.fixture
def world_ddb(dynamodb):
    client, table = dynamodb
    conv = DynamoConversationStore(client, table)
    interview = DynamoInterviewStore(client, table)
    deliveries = DynamoDeliveryStore(client, table)
    service = ConversationService(
        conv, UnavailableProducer(), interview=interview, deliveries=deliveries, clock=lambda: START
    )
    sessions = DynamoSessionStore(client, table)
    access = AccessService(None, sessions)  # ending a session never reads a secret

    def begin(session_id, started=START):
        """A signed-in session that said something, finished a reply, drafted and downloaded."""
        session = Session(session_id, started, started + LIFETIME)
        sessions.create(session)
        sent = conv.submit(
            session_id, f"sub-{session_id}", "My idea", 0, session.expires_at, started
        )
        conv.complete_turn(session_id, sent.assistant_turn.turn_id, "A reply")
        interview.put(
            session_id, InterviewState(canvas={"team": "Ops"}), session.expires_at, started
        )
        interview.confirm(session_id, 1, session.expires_at, started)
        deliveries.record(session_id, "canvas", 1, started, session.expires_at)
        return session

    return client, table, service, access, sessions, begin


def test_the_sweep_leaves_no_trace_of_an_expired_session_anywhere_in_the_table(world_ddb):
    client, table, service, access, sessions, begin = world_ddb
    begin("old", START - timedelta(hours=30))
    live = begin("live", START)
    assert {i["sk"]["S"] for i in items_of(client, table, "old")} == {
        "ACCESS", "CONV", "TURN#0001", "TURN#0002", "INTERVIEW", "DELIVERY#canvas",
    }  # fmt: skip
    live_before = items_of(client, table, "live")

    result = run_expiry(service, access, now=START)

    assert (result.found, result.deleted, result.failed) == (1, 1, 0) and result.complete
    assert items_of(client, table, "old") == []  # the canary scan: zero matches
    assert items_of(client, table, "live") == live_before  # a live session is untouched
    assert sessions.get("old") is None and sessions.get("live") == live
    assert [t for t in service._store.history("old")[1]] == []


def test_a_session_whose_sign_in_record_is_already_gone_is_still_found_and_removed(world_ddb):
    """TTL may remove the sign-in record first; the content must not be left behind."""
    client, table, service, access, sessions, begin = world_ddb
    begin("orphan", START - timedelta(hours=30))
    sessions.delete("orphan")  # what an early TTL deletion looks like
    result = run_expiry(service, access, now=START)
    assert result.deleted == 1 and items_of(client, table, "orphan") == []


def test_many_sessions_are_swept_across_pages_and_a_rerun_finds_nothing(world_ddb):
    client, table, service, access, sessions, begin = world_ddb
    for i in range(7):
        begin(f"s{i}", START - timedelta(hours=30 + i))
    first = run_expiry(service, access, now=START, page_size=3)
    assert (first.found, first.deleted, first.complete) == (7, 7, True)
    assert all(items_of(client, table, f"s{i}") == [] for i in range(7))
    assert run_expiry(service, access, now=START + timedelta(minutes=15)).found == 0


def test_the_24_hour_boundary_holds_on_dynamodb(world_ddb):
    _, _, service, access, _, begin = world_ddb
    begin("edge", START)
    assert run_expiry(service, access, now=START + LIFETIME).found == 0
    assert run_expiry(service, access, now=START + LIFETIME + timedelta(seconds=1)).deleted == 1


def test_a_repeat_or_overlapping_sweep_is_harmless(world_ddb):
    _, _, service, access, _, begin = world_ddb
    begin("a", START - timedelta(hours=30))
    one = run_expiry(service, access, now=START)
    two = run_expiry(service, access, now=START)
    assert one.deleted == 1 and two.found == 0 and two.ok


# --- the sign-in store on the shared table --------------------------------------------------------


def test_sign_in_records_use_the_shared_table_with_their_own_sort_key(dynamodb):
    client, table = dynamodb
    store = DynamoSessionStore(client, table)
    session = Session("sid-1", START, START + LIFETIME)
    store.create(session)
    item = items_of(client, table, "sid-1")[0]
    assert item["sk"] == {"S": "ACCESS"}
    assert item["expires_at"] == {
        "N": str(int(session.expires_at.timestamp()))
    }  # the TTL attribute
    assert store.get("sid-1") == session and store.get("missing") is None
    with pytest.raises(DependencyUnavailable):  # an id collision is refused, never overwritten
        store.create(Session("sid-1", START, START + timedelta(days=9)))
    store.delete("sid-1")
    assert store.get("sid-1") is None


def test_grants_are_single_use_and_cannot_be_mistaken_for_sessions(dynamodb):
    client, table = dynamodb
    store = DynamoSessionStore(client, table)
    assert store.claim_grant("g1", START + timedelta(minutes=10), START) is True
    assert store.claim_grant("g1", START + timedelta(minutes=10), START) is False
    assert store.get("g1") is None
    assert items_of(client, table, "grant#g1")[0]["sk"] == {"S": "GRANT"}


def test_the_same_session_id_holds_each_kind_of_item_without_clashing(dynamodb):
    client, table = dynamodb
    sessions = DynamoSessionStore(client, table)
    conv = DynamoConversationStore(client, table)
    sessions.create(Session("sid", START, START + LIFETIME))
    conv.submit("sid", "sub", "hi", 0, START + LIFETIME, START)
    assert sessions.get("sid") is not None and conv.history("sid")[0] == 1


# --- deployed wiring ---------------------------------------------------------------------------------


def deployed_settings(tmp_path, **kwargs):
    (tmp_path / "index.html").write_text("<!doctype html>")
    return Settings(
        app_env=AppEnv.DEPLOYED,
        static_dir=tmp_path,
        sessions_table_name="plan-prod-sessions",
        access_code_hash_secret_id="hash",
        cookie_signing_key_secret_id="key",
        **kwargs,
    )


def test_a_deployed_app_keeps_everything_in_the_one_dynamodb_table(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    service = build_conversation(deployed_settings(tmp_path))  # the sweeper builds it exactly so
    assert isinstance(service._store, DynamoConversationStore)
    assert isinstance(
        service._interview, DynamoInterviewStore
    )  # attached, so a delete clears drafts
    assert isinstance(service._deliveries, DynamoDeliveryStore)
    assert service._store._table == service._interview._table == "plan-prod-sessions"
    assert service._store._client.meta.region_name == "ap-southeast-2"


def test_a_local_app_still_uses_memory(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    service = build_conversation(Settings(app_env=AppEnv.LOCAL, static_dir=tmp_path))
    assert (
        type(service._store).__name__ == "InMemoryConversationStore" and service._interview is None
    )


def test_the_table_the_tests_build_is_the_documented_layout():
    definition = dynamo.table_definition("t")
    assert definition["BillingMode"] == "PAY_PER_REQUEST"
    assert [k["AttributeName"] for k in definition["KeySchema"]] == ["session_id", "sk"]
    (index,) = definition["GlobalSecondaryIndexes"]
    assert index["IndexName"] == "expiry-index" and index["Projection"] == {
        "ProjectionType": "KEYS_ONLY"
    }
    assert [k["AttributeName"] for k in index["KeySchema"]] == ["gsi_pk", "expires_at"]
