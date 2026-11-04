"""Scheduled expiry: sessions strictly older than 24 hours go, with all their content."""

import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app import expiry
from app.access.errors import DependencyUnavailable
from app.conversation.producers import UnavailableProducer
from app.conversation.service import ConversationService
from app.conversation.store import InMemoryConversationStore, UnavailableConversationStore
from app.expiry import run_expiry
from app.interview.store import InMemoryInterviewStore

START = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
LIFETIME = timedelta(hours=24)


class Access:
    """TEST DOUBLE for the sign-in service: remembers which sessions it was told to end."""

    def __init__(self, fail_for=()):
        self.ended, self.fail_for = [], set(fail_for)

    def end_session(self, session_id):
        if session_id in self.fail_for:
            raise RuntimeError("SECRET-DETAIL")
        self.ended.append(session_id)


def service():
    return ConversationService(
        InMemoryConversationStore(),
        UnavailableProducer(),
        interview=InMemoryInterviewStore(),
        clock=lambda: START,
    )


def begin(svc, session_id, started=START):
    """A session that began at `started` and said one thing; it ends 24 hours after it began."""
    svc._store.submit(session_id, f"sub-{session_id}", "My idea", 0, started + LIFETIME, started)
    svc._deliveries.record(session_id, "canvas", 1, started, started + LIFETIME)


def alive(svc, session_id):
    return svc._store.history(session_id)[1] != []


def ids(n):
    return [f"s{i:02d}" for i in range(n)]


# --- the 24 hour boundary --------------------------------------------------------------------------------


def test_a_session_exactly_24_hours_old_is_kept_and_one_second_older_goes():
    svc, access = service(), Access()
    begin(svc, "edge")
    assert run_expiry(svc, access, now=START + LIFETIME).found == 0  # exactly 24 h: not over yet
    assert alive(svc, "edge")
    result = run_expiry(svc, access, now=START + LIFETIME + timedelta(seconds=1))
    assert (result.found, result.deleted, result.failed) == (1, 1, 0) and result.ok
    assert not alive(svc, "edge") and access.ended == ["edge"]


def test_only_expired_sessions_are_removed_and_live_ones_are_left_alone():
    svc, access = service(), Access()
    # Newest first: the in-memory store forgets expired sessions whenever a later one is written.
    begin(svc, "new", START - timedelta(hours=2))
    begin(svc, "old", START - timedelta(hours=30))
    result = run_expiry(svc, access, now=START)
    assert result.deleted == 1 and not alive(svc, "old") and alive(svc, "new")
    assert access.ended == ["old"]


def test_an_abandoned_session_loses_everything_without_any_download_or_confirmation():
    svc, access = service(), Access()
    begin(svc, "gone", START - timedelta(hours=25))
    svc._interview.put("gone", svc._interview.get("gone"), START + timedelta(hours=1), START)
    run_expiry(svc, access, now=START)
    assert svc._store.history("gone") == (0, [])
    assert svc._deliveries.get("gone") == {}
    assert svc._interview.get("gone").canvas == {}


# --- pagination, repeats and overlap ---------------------------------------------------------------------


def test_every_page_is_read_and_a_sweep_covers_them_all():
    svc, access = service(), Access()
    for session_id in ids(7):
        begin(svc, session_id, START - timedelta(hours=30))
    result = run_expiry(svc, access, now=START, page_size=3)
    assert (result.found, result.deleted, result.complete) == (7, 7, True)
    assert sorted(access.ended) == ids(7)


def test_a_sweep_that_hits_its_page_limit_says_so_and_the_next_one_finishes():
    svc, access = service(), Access()
    for session_id in ids(7):
        begin(svc, session_id, START - timedelta(hours=30))
    first = run_expiry(svc, access, now=START, page_size=3, max_pages=1)
    assert (first.deleted, first.complete) == (3, False)
    second = run_expiry(svc, access, now=START + timedelta(minutes=15), page_size=3, max_pages=5)
    assert second.complete and first.deleted + second.deleted == 7
    assert not any(alive(svc, session_id) for session_id in ids(7))


def test_running_it_again_finds_nothing_more_to_do():
    svc, access = service(), Access()
    begin(svc, "a", START - timedelta(hours=30))
    run_expiry(svc, access, now=START)
    again = run_expiry(svc, access, now=START + timedelta(minutes=15))
    assert (again.found, again.deleted, again.failed) == (0, 0, 0) and again.ok


def test_two_sweeps_at_once_do_not_collide():
    svc = service()
    for session_id in ids(30):
        begin(svc, session_id, START - timedelta(hours=30))
    results, errors = [], []

    def sweep():
        try:
            results.append(run_expiry(svc, Access(), now=START, page_size=4))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=sweep) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and all(r.ok for r in results)
    assert not any(alive(svc, session_id) for session_id in ids(30))


def test_deleting_one_session_twice_is_harmless():
    svc = service()
    begin(svc, "a", START - timedelta(hours=30))
    svc.delete_session("a")
    svc.delete_session("a")
    assert not alive(svc, "a")


# --- partial failure -----------------------------------------------------------------------------------


def test_one_failing_session_does_not_stop_the_rest_and_is_retried_next_time(caplog):
    svc = service()
    for session_id in ids(4):
        begin(svc, session_id, START - timedelta(hours=30))
    result = run_expiry(svc, Access(fail_for={"s01"}), now=START, page_size=2)
    assert (result.found, result.deleted, result.failed) == (4, 3, 1) and not result.ok
    assert alive(svc, "s01") and not alive(svc, "s00") and not alive(svc, "s03")
    retry = run_expiry(svc, Access(), now=START + timedelta(minutes=15))
    assert (retry.found, retry.deleted, retry.failed) == (1, 1, 0)
    assert not alive(svc, "s01")


def test_the_sign_in_record_is_ended_before_the_content_is_deleted():
    svc, order = service(), []
    begin(svc, "a", START - timedelta(hours=30))
    real = svc.delete_session

    class Ordered(Access):
        def end_session(self, session_id):
            order.append(("access", alive(svc, session_id)))  # content still there at this point
            super().end_session(session_id)

    svc.delete_session = lambda sid: (order.append(("content", None)), real(sid))[1]
    run_expiry(svc, Ordered(), now=START)
    assert order == [("access", True), ("content", None)]


def test_a_failure_is_logged_by_type_only():
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    svc = service()
    begin(svc, "a", START - timedelta(hours=30))
    logger = logging.getLogger("app.expiry")
    handler = Collect()
    logger.addHandler(handler)
    try:
        run_expiry(svc, Access(fail_for={"a"}), now=START)
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage() for r in records] == ["expiry_delete_failed"]
    assert "SECRET" not in repr([r.__dict__ for r in records])
    assert records[0].exception_type == "RuntimeError"


# --- the command -----------------------------------------------------------------------------------------


@pytest.fixture
def command(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def collect(name="app.expiry"):
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger(name)
    handler = Collect()
    logger.addHandler(handler)
    return records, lambda: logger.removeHandler(handler)


def test_the_command_exits_cleanly_and_reports_counts_only(command, monkeypatch):
    monkeypatch.setattr(
        "app.access.wiring.build_access_service", lambda settings: Access(), raising=True
    )
    records, stop = collect()
    try:
        assert expiry.main() == 0
    finally:
        stop()
    (run,) = [r for r in records if r.getMessage() == "expiry_run"]
    assert run.detail == "found=0 deleted=0 failed=0 complete=True"


def test_the_command_fails_loudly_when_there_is_no_store_to_sweep(command, monkeypatch):
    monkeypatch.setattr(
        "app.access.wiring.build_access_service", lambda settings: Access(), raising=True
    )
    monkeypatch.setattr(
        "app.main.build_conversation",
        lambda settings: ConversationService(UnavailableConversationStore(), UnavailableProducer()),
    )
    records, stop = collect()
    try:
        assert expiry.main() == 1
    finally:
        stop()
    assert [r.getMessage() for r in records] == ["expiry_unavailable"]


def test_the_command_exits_non_zero_when_a_session_could_not_be_removed(command, monkeypatch):
    svc = service()
    begin(svc, "a", datetime.now(UTC) - timedelta(hours=30))
    monkeypatch.setattr(
        "app.access.wiring.build_access_service", lambda s: Access(fail_for={"a"}), raising=True
    )
    monkeypatch.setattr("app.main.build_conversation", lambda settings: svc)
    assert expiry.main() == 1


def test_an_unavailable_store_raises_the_dependency_error_for_the_sweep_itself():
    svc = ConversationService(UnavailableConversationStore(), UnavailableProducer())
    with pytest.raises(DependencyUnavailable):
        run_expiry(svc, Access(), now=START)
