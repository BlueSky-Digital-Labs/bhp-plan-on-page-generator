"""Delivery records: idempotent, never backwards, scoped to a session, ending with it."""

from datetime import UTC, datetime, timedelta

import pytest

from app.access.errors import DependencyUnavailable
from app.delivery import InMemoryDeliveryStore, UnavailableDeliveryStore

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
END = NOW + timedelta(hours=24)


def test_a_record_is_kept_per_session_and_document():
    store = InMemoryDeliveryStore(clock=lambda: NOW)
    assert store.record("s1", "canvas", 3, NOW, END)
    assert store.record("s1", "appendix", 3, NOW, END)
    got = store.get("s1")
    assert {k: v.revision for k, v in got.items()} == {"canvas": 3, "appendix": 3}
    assert got["canvas"].completed_at == NOW and got["canvas"].kind == "canvas"
    assert store.get("s2") == {}  # another session sees nothing


def test_the_same_delivery_again_is_harmless_and_keeps_the_first_time():
    store = InMemoryDeliveryStore(clock=lambda: NOW)
    store.record("s1", "canvas", 3, NOW, END)
    assert store.record("s1", "canvas", 3, NOW + timedelta(minutes=5), END)
    assert store.get("s1")["canvas"].completed_at == NOW


def test_a_newer_revision_replaces_and_an_older_one_is_ignored():
    store = InMemoryDeliveryStore(clock=lambda: NOW)
    store.record("s1", "canvas", 3, NOW, END)
    assert store.record("s1", "canvas", 5, NOW, END)
    assert store.record("s1", "canvas", 4, NOW, END) is False  # a late, older completion
    assert store.get("s1")["canvas"].revision == 5


def test_the_two_documents_are_independent():
    store = InMemoryDeliveryStore(clock=lambda: NOW)
    store.record("s1", "canvas", 3, NOW, END)
    assert "appendix" not in store.get("s1")
    store.record("s1", "appendix", 2, NOW, END)
    assert store.get("s1")["canvas"].revision == 3 and store.get("s1")["appendix"].revision == 2


def test_the_returned_records_are_a_copy():
    store = InMemoryDeliveryStore(clock=lambda: NOW)
    store.record("s1", "canvas", 3, NOW, END)
    store.get("s1").clear()
    assert "canvas" in store.get("s1")


def test_records_end_with_their_session():
    now = [NOW]
    store = InMemoryDeliveryStore(clock=lambda: now[0])
    store.record("s1", "canvas", 3, NOW, END)
    now[0] = END  # s1 has ended; the next write clears it
    store.record("s2", "canvas", 1, END, END + timedelta(hours=24))
    assert store.get("s1") == {}


def test_ended_sessions_are_judged_by_the_clock_not_the_write_time():
    now = [END]
    store = InMemoryDeliveryStore(clock=lambda: now[0])
    store.record("s1", "canvas", 3, NOW, END + timedelta(hours=1))  # live at END
    now[0] = END + timedelta(hours=2)  # s1 has ended
    store.record("s2", "canvas", 1, NOW, END + timedelta(hours=26))  # an old write time
    assert store.get("s1") == {} and "canvas" in store.get("s2")


def test_a_full_table_refuses_new_sessions_and_never_drops_live_ones():
    store = InMemoryDeliveryStore(capacity=2, clock=lambda: NOW)
    assert store.record("a", "canvas", 1, NOW, END) and store.record("b", "canvas", 1, NOW, END)
    assert store.record("c", "canvas", 1, NOW, END) is False
    assert store.record("a", "appendix", 1, NOW, END)  # an existing session keeps working
    assert "canvas" in store.get("a") and "canvas" in store.get("b")


def test_deleting_a_session_removes_everything_and_is_repeatable():
    store = InMemoryDeliveryStore(clock=lambda: NOW)
    store.record("s1", "canvas", 3, NOW, END)
    store.delete_session("s1")
    store.delete_session("s1")
    assert store.get("s1") == {}


def test_the_unavailable_store_fails_closed_but_can_always_be_emptied():
    store = UnavailableDeliveryStore()
    with pytest.raises(DependencyUnavailable):
        store.get("s1")
    with pytest.raises(DependencyUnavailable):
        store.record("s1", "canvas", 1, NOW, END)
    store.delete_session("s1")
