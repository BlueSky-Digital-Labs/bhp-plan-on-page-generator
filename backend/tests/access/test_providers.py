import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.access.errors import DependencyUnavailable
from app.access.secrets import EnvSecretProvider, SecretsManagerProvider
from app.access.store import DynamoSessionStore, InMemorySessionStore, Session
from tests.ai.conftest import SECRET

LEAKY = {"Error": {"Code": "X", "Message": SECRET}, "ResponseMetadata": {}}


class FakeSecretsManager:
    def __init__(self, values):
        self.values, self.calls, self.error = dict(values), [], None

    def get_secret_value(self, SecretId):
        self.calls.append(SecretId)
        if self.error:
            raise self.error
        return {"SecretString": self.values[SecretId]}


class Ticker:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def provider(values=None, **kw):
    sm = FakeSecretsManager(values or {"hash-id": "$argon2$h", "key-id": "k" * 40})
    return sm, SecretsManagerProvider(sm, "hash-id", "key-id", **kw)


def test_access_code_hash_is_fetched_on_every_call_never_cached():
    sm, p = provider()
    assert p.access_code_hash() == "$argon2$h"
    sm.values["hash-id"] = "$argon2$rotated"  # rotation in Secrets Manager
    assert p.access_code_hash() == "$argon2$rotated"  # visible immediately, no restart
    assert sm.calls.count("hash-id") == 2


def test_signing_key_is_cached_briefly_then_refreshed():
    ticker = Ticker()
    sm, p = provider(clock=ticker)
    p.cookie_signing_key()
    p.cookie_signing_key()
    assert sm.calls.count("key-id") == 1
    ticker.t = 61
    p.cookie_signing_key()
    assert sm.calls.count("key-id") == 2


def test_concurrent_requests_share_one_signing_key_fetch():
    sm, p = provider()
    fetch = sm.get_secret_value

    def slow_fetch(SecretId):
        time.sleep(0.05)  # keep the first fetch in flight while the other threads arrive
        return fetch(SecretId)

    sm.get_secret_value = slow_fetch
    keys = []
    threads = [
        threading.Thread(target=lambda: keys.append(p.cookie_signing_key())) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert keys == [b"k" * 40] * 8
    assert sm.calls.count("key-id") == 1


def test_a_weak_signing_key_is_refused():
    _, p = provider({"hash-id": "h", "key-id": "short"})
    with pytest.raises(DependencyUnavailable) as excinfo:
        p.cookie_signing_key()
    assert excinfo.value.category == "signing_key_invalid"


@pytest.mark.parametrize(
    "error",
    [ClientError(LEAKY, "GetSecretValue"), EndpointConnectionError(endpoint_url="https://x")],
)
def test_lookup_failures_raise_dependency_unavailable_without_provider_text(error):
    sm, p = provider()
    sm.error = error
    with pytest.raises(DependencyUnavailable) as excinfo:
        p.access_code_hash()
    assert str(excinfo.value) == "secret_lookup" and SECRET not in repr(excinfo.value)
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__


def test_missing_secret_string_is_a_failure_not_an_empty_secret():
    class Binary:
        def get_secret_value(self, SecretId):
            return {"SecretBinary": b"x"}  # no SecretString

    with pytest.raises(DependencyUnavailable):
        SecretsManagerProvider(Binary(), "h", "k").access_code_hash()


def test_env_provider_is_local_only_and_fails_closed_when_unset(monkeypatch):
    with pytest.raises(DependencyUnavailable):
        EnvSecretProvider().access_code_hash()
    monkeypatch.setenv("ACCESS_CODE_HASH", "$argon2$env")
    monkeypatch.setenv("COOKIE_SIGNING_KEY", "k" * 40)
    assert EnvSecretProvider().access_code_hash() == "$argon2$env"
    assert EnvSecretProvider().cookie_signing_key() == b"k" * 40


# --- DynamoDB store ------------------------------------------------------------------


class FakeDynamo:
    def __init__(self):
        self.items, self.calls, self.error = {}, [], None

    def put_item(self, TableName, Item, ConditionExpression):
        self.calls.append(("put", TableName, ConditionExpression))
        if self.error:
            raise self.error
        key = Item["session_id"]["S"]
        if key in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": SECRET}},
                "PutItem",
            )
        self.items[key] = Item

    def get_item(self, TableName, Key, ConsistentRead):
        self.calls.append(("get", ConsistentRead))
        if self.error:
            raise self.error
        item = self.items.get(Key["session_id"]["S"])
        return {"Item": item} if item else {}

    def delete_item(self, TableName, Key):
        self.items.pop(Key["session_id"]["S"], None)


NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
SESSION = Session("sid-1", NOW, NOW + timedelta(hours=24))


def test_create_writes_numeric_epoch_ttl_and_never_overwrites():
    client = FakeDynamo()
    store = DynamoSessionStore(client, "sessions")
    store.create(SESSION)
    item = client.items["sid-1"]
    assert item["expires_at"] == {"N": str(int(SESSION.expires_at.timestamp()))}  # TTL attribute
    assert item["created_at"]["S"] == NOW.isoformat()
    assert client.calls[0][2] == "attribute_not_exists(session_id)"
    with pytest.raises(DependencyUnavailable):  # id collision: refuse, don't overwrite
        store.create(Session("sid-1", NOW, NOW + timedelta(days=9)))
    assert client.items["sid-1"] == item


def test_get_uses_consistent_reads_and_round_trips():
    client = FakeDynamo()
    store = DynamoSessionStore(client, "sessions")
    store.create(SESSION)
    assert store.get("sid-1") == SESSION
    assert ("get", True) in client.calls
    assert store.get("missing") is None


def test_grants_are_single_use_and_namespaced_away_from_sessions():
    client = FakeDynamo()
    store = DynamoSessionStore(client, "sessions")
    assert store.claim_grant("g1", NOW + timedelta(minutes=10), NOW) is True
    assert store.claim_grant("g1", NOW + timedelta(minutes=10), NOW) is False
    assert "grant#g1" in client.items and store.get("g1") is None


@pytest.mark.parametrize(
    "error", [ClientError(LEAKY, "Op"), EndpointConnectionError(endpoint_url="u")]
)
def test_store_failures_fail_closed_without_provider_text(error):
    client = FakeDynamo()
    store = DynamoSessionStore(client, "sessions")
    client.error = error
    for call in (
        lambda: store.create(SESSION),
        lambda: store.get("x"),
        lambda: store.claim_grant("g", NOW, NOW),
    ):
        with pytest.raises(DependencyUnavailable) as excinfo:
            call()
        assert SECRET not in repr(excinfo.value)


def test_corrupt_record_fails_closed_instead_of_granting_access():
    client = FakeDynamo()
    client.items["sid-1"] = {"session_id": {"S": "sid-1"}}  # missing expiry
    with pytest.raises(DependencyUnavailable):
        DynamoSessionStore(client, "sessions").get("sid-1")


# --- in-memory store: bounded memory, without weakening single use ---------------------


def test_in_memory_store_forgets_expired_grants_but_never_an_unexpired_one():
    store = InMemorySessionStore()
    soon = NOW + timedelta(minutes=10)
    assert store.claim_grant("old", soon, NOW) is True
    assert store.claim_grant("live", NOW + timedelta(hours=1), NOW) is True

    later = soon + timedelta(seconds=1)  # "old" has expired; "live" has not
    assert store.claim_grant("new", later + timedelta(minutes=10), later) is True
    assert set(store._grants) == {"live", "new"}  # the expired claim was dropped
    assert store.claim_grant("live", NOW + timedelta(hours=1), later) is False  # still single use


def test_in_memory_store_drops_expired_sessions_when_new_ones_arrive():
    store = InMemorySessionStore()
    first = Session("s1", NOW, NOW + timedelta(hours=24))
    store.create(first)
    mid = NOW + timedelta(hours=1)
    store.create(Session("s2", mid, mid + timedelta(hours=24)))
    assert set(store.sessions) == {"s1", "s2"}  # nothing expired yet: nothing dropped

    after = first.expires_at + timedelta(seconds=1)
    store.create(Session("s3", after, after + timedelta(hours=24)))
    assert set(store.sessions) == {"s2", "s3"}  # s1 expired and was pruned
