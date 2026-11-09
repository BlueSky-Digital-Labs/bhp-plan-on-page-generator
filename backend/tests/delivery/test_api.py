"""Download status and deletion through the real app: the server's gate decides everything.

Every test runs twice: on the in-memory stores and on DynamoDB (moto), so the deployed storage is held
to exactly the same behaviour.
"""

import logging
import uuid
from datetime import timedelta

import pytest

from app.access.errors import DependencyUnavailable
from tests.access.conftest import csrf
from tests.appendix.test_appendix_api import appendix, finish_confirmed
from tests.appendix.test_canvas_pptx import canvas_file
from tests.interview.conftest import OPENING, browser
from tests.interview.test_interview_api import answer_for, history, say, wire

pytestmark = pytest.mark.usefixtures("store_kind")


def status(client):
    return client.get("/api/downloads")


def purge(client, revision=None, headers=None, **extra):
    revision = history(client)["revision"] if revision is None else revision
    return client.post(
        "/api/session/purge",
        json={"revision": revision, **extra},
        headers=csrf(client) if headers is None else headers,
    )


def code(response) -> str:
    return response.json()["error"]["code"]


def both_downloaded(client):
    assert canvas_file(client, "pdf").status_code == 200
    assert appendix(client, "pdf").status_code == 200


def session_of(world):
    return next(iter(world.store.sessions.values()))


def ready_client(world):
    wire(world)
    client = browser(world)
    finish_confirmed(client)
    return client


# --- the status ---------------------------------------------------------------------------------------


def test_status_follows_each_download_and_names_what_is_outstanding(world):
    client = ready_client(world)
    revision = history(client)["revision"]
    assert status(client).json() == {
        "revision": revision,
        "canvas_downloaded": False,
        "appendix_downloaded": False,
        "purge_eligible": False,
        "lock_reason": "both_not_downloaded",
        "complete": True,
    }
    assert canvas_file(client, "pdf").status_code == 200
    got = status(client).json()
    assert got["canvas_downloaded"] and not got["appendix_downloaded"]
    assert got["lock_reason"] == "appendix_not_downloaded" and not got["purge_eligible"]
    assert appendix(client, "pdf").status_code == 200
    got = status(client).json()
    assert got["appendix_downloaded"] and got["purge_eligible"] and got["lock_reason"] is None


def test_status_is_never_cached_and_needs_a_session(world):
    client = ready_client(world)
    assert status(client).headers["cache-control"] == "no-store"
    client.cookies.clear()
    assert status(client).status_code in (401, 403)


def test_any_format_of_a_document_counts(world):
    client = ready_client(world)
    assert canvas_file(client, "pptx").status_code == 200
    got = status(client).json()
    assert got["canvas_downloaded"] and not got["appendix_downloaded"]
    assert got["lock_reason"] == "appendix_not_downloaded" and not got["purge_eligible"]
    assert appendix(client, "docx").status_code == 200
    got = status(client).json()
    assert got["canvas_downloaded"] and got["appendix_downloaded"]
    assert got["purge_eligible"] and got["lock_reason"] is None


def test_the_json_appendix_also_counts_for_the_appendix(world):
    client = ready_client(world)
    assert canvas_file(client, "pdf").status_code == 200
    assert appendix(client, "json").status_code == 200
    assert status(client).json()["purge_eligible"]


def test_asking_for_a_file_without_receiving_it_records_nothing(world):
    client = ready_client(world)
    revision = history(client)["revision"]
    assert canvas_file(client, "pdf", revision + 1).status_code == 409  # refused: wrong revision
    assert appendix(client, "pdf", revision + 1).status_code == 409
    assert canvas_file(client, "xyz").status_code == 422
    got = status(client).json()
    assert not got["canvas_downloaded"] and not got["appendix_downloaded"]


def test_downloading_again_changes_nothing(world):
    client = ready_client(world)
    both_downloaded(client)
    first = status(client).json()
    both_downloaded(client)
    assert status(client).json() == first and first["purge_eligible"]


def test_with_nothing_answered_there_is_nothing_to_save_and_no_deletion(world):
    wire(world)
    client = browser(world)
    got = status(client).json()
    assert got["lock_reason"] == "not_ready" and not got["purge_eligible"]
    assert purge(client).status_code == 409


def test_an_unfinished_draft_is_saved_then_the_session_can_be_stopped(world):
    wire(world)
    client = browser(world)
    say(client, OPENING)  # one answer in, the canvas still incomplete
    assert history(client)["canvas"]["ready_for_review"] is False
    got = status(client).json()
    assert got["complete"] is False and got["lock_reason"] == "both_not_downloaded"
    assert purge(client).status_code == 409  # nothing saved yet: still locked
    assert canvas_file(client, "pdf").status_code == 200
    assert status(client).json()["lock_reason"] == "appendix_not_downloaded"
    assert appendix(client, "docx").status_code == 200  # any format counts
    got = status(client).json()
    assert got["purge_eligible"] and got["lock_reason"] is None and got["complete"] is False
    session = session_of(world)
    assert purge(client).status_code == 204
    # the backend deletes everything, exactly as for a finished canvas, and ends the sign-in
    assert world.conversation._store.history(session.session_id) == (0, [])
    assert world.conversation._interview.get(session.session_id).canvas == {}
    assert world.conversation._deliveries.get(session.session_id) == {}
    assert session.session_id not in world.store.sessions
    assert status(client).status_code in (401, 403)


def test_an_answer_after_saving_a_draft_means_saving_again_before_stopping(world):
    wire(world)
    client = browser(world)
    reply, _ = say(client, OPENING)
    both_downloaded(client)
    assert status(client).json()["purge_eligible"]
    say(client, answer_for(reply))  # carried on after saving
    got = status(client).json()
    assert not got["purge_eligible"] and got["lock_reason"] == "both_not_downloaded"
    assert purge(client).status_code == 409


def test_the_unfinished_canvas_still_cannot_be_emailed(world):
    from tests.mailer.test_email_api import mailing, send

    wire(world)
    mailer = mailing(world)
    client = browser(world)
    say(client, OPENING)
    both_downloaded(client)  # saved as a draft
    response = send(client)
    assert response.status_code == 409 and code(response) == "canvas_not_ready"
    assert not mailer.sent


def test_an_amendment_resets_both_documents(world):
    client = ready_client(world)
    both_downloaded(client)
    assert status(client).json()["purge_eligible"]
    say(client, "One more detail.")  # a new revision
    got = status(client).json()
    assert not got["canvas_downloaded"] and not got["appendix_downloaded"]
    assert not got["purge_eligible"] and got["revision"] == history(client)["revision"]


def test_a_download_that_finishes_after_an_amendment_unlocks_nothing(world):
    client = ready_client(world)
    old = history(client)["revision"]
    say(client, "Another answer arrives while the old file is still being sent.")
    service, session = world.conversation, session_of(world)
    assert service.record_delivery(session, "canvas", old) is False  # the late completion
    assert service.record_delivery(session, "appendix", old) is False
    got = status(client).json()
    assert not got["canvas_downloaded"] and not got["appendix_downloaded"]


def test_one_session_sees_only_its_own_status(world):
    client = ready_client(world)
    both_downloaded(client)
    other = browser(world)
    got = status(other).json()
    assert not got["canvas_downloaded"] and not got["purge_eligible"]  # not the first one's


def test_a_failure_to_note_delivery_still_delivers_the_file_but_unlocks_nothing(world, monkeypatch):
    client = ready_client(world)

    def broken(*_args, **_kwargs):
        raise DependencyUnavailable("store")

    monkeypatch.setattr(world.conversation._deliveries, "record", broken)
    response = canvas_file(client, "pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    monkeypatch.undo()
    assert not status(client).json()["canvas_downloaded"]


def test_if_the_status_cannot_be_read_it_says_so_and_unlocks_nothing(world, monkeypatch):
    client = ready_client(world)
    both_downloaded(client)

    def broken(*_args, **_kwargs):
        raise DependencyUnavailable("store")

    monkeypatch.setattr(world.conversation._deliveries, "get", broken)
    response = status(client)
    assert response.status_code == 503 and "purge_eligible" not in response.text
    assert purge(client).status_code == 503
    monkeypatch.undo()
    assert history(client)["turns"]  # nothing was deleted
    assert status(client).json()["purge_eligible"]


# --- deleting ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["none", "canvas", "appendix"])
def test_deleting_is_refused_until_both_pdfs_are_downloaded_and_changes_nothing(world, which):
    client = ready_client(world)
    if which == "canvas":
        assert canvas_file(client, "pdf").status_code == 200
    if which == "appendix":
        assert appendix(client, "pdf").status_code == 200
    before = history(client)
    response = purge(client)
    assert response.status_code == 409 and code(response) == "purge_locked"
    assert "set-cookie" not in response.headers  # the session was not touched
    assert history(client) == before
    assert status(client).status_code == 200  # still signed in


def test_deleting_after_both_downloads_removes_everything_and_ends_the_session(world):
    client = ready_client(world)
    both_downloaded(client)
    service, session = world.conversation, session_of(world)
    revision = history(client)["revision"]
    response = purge(client, revision)
    assert response.status_code == 204 and response.content == b""
    assert "__Host-session" in response.headers["set-cookie"]  # the cookie is cleared
    assert service._store.history(session.session_id) == (0, [])
    assert service._interview.get(session.session_id).canvas == {}
    assert service._deliveries.get(session.session_id) == {}
    assert session.session_id not in world.store.sessions
    assert status(client).status_code in (401, 403)  # the session is gone


def test_a_purge_for_the_wrong_revision_is_refused(world):
    client = ready_client(world)
    both_downloaded(client)
    revision = history(client)["revision"]
    for wrong in (revision - 1, revision + 1):
        response = purge(client, wrong)
        assert response.status_code == 409 and code(response) == "revision_conflict"
    assert history(client)["turns"] and status(client).json()["purge_eligible"]


def test_an_answer_after_the_gate_was_read_stops_the_deletion(world, monkeypatch):
    """Another tab answers between the gate check and the delete: the newer work is not lost."""
    client = ready_client(world)
    both_downloaded(client)
    service = world.conversation
    revision = history(client)["revision"]
    real = service.download_gate

    def gate_then_race(sess):
        result = real(sess)
        service._store.submit(
            sess.session_id,
            uuid.uuid4().hex,
            "A late change.",
            revision,
            world.clock() + timedelta(hours=1),
            world.clock(),
        )
        return result

    monkeypatch.setattr(service, "download_gate", gate_then_race)
    response = purge(client, revision)
    assert response.status_code == 409 and code(response) == "revision_conflict"
    monkeypatch.undo()
    texts = [t["text"] for t in history(client)["turns"]]
    assert "A late change." in texts  # still there


def test_the_client_cannot_claim_eligibility_or_add_fields(world):
    client = ready_client(world)
    revision = history(client)["revision"]
    for extra in ({"purge_eligible": True}, {"canvas_downloaded": True}, {"force": True}):
        assert purge(client, revision, **extra).status_code == 422
    assert history(client)["turns"]


def test_deleting_needs_a_session_and_the_forgery_protections(world):
    client = ready_client(world)
    both_downloaded(client)
    revision = history(client)["revision"]
    assert purge(client, revision, headers={}).status_code == 403
    assert (
        purge(
            client, revision, headers={**csrf(client), "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    token = csrf(client)
    client.cookies.clear()
    assert purge(client, revision, headers=token).status_code in (401, 403)
    assert world.conversation._store.history(session_of(world).session_id)[1]  # untouched


def test_one_session_cannot_delete_another_sessions_work(world):
    client = ready_client(world)
    both_downloaded(client)
    revision = history(client)["revision"]
    other = browser(world)
    response = purge(other, revision)
    assert response.status_code == 409  # the other session has nothing downloaded or confirmed
    assert history(client)["turns"]


def test_a_purged_session_is_logged_without_content(world):
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    client = ready_client(world)
    both_downloaded(client)
    logger = logging.getLogger("app.access")
    handler = Collect()
    logger.addHandler(handler)
    try:
        purge(client)
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage() for r in records] == ["session_purged"]


def test_a_refused_purge_is_logged_with_its_reason(world):
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    client = ready_client(world)
    logger = logging.getLogger("app.access")
    handler = Collect()
    logger.addHandler(handler)
    try:
        purge(client)
    finally:
        logger.removeHandler(handler)
    assert [(r.getMessage(), r.reason) for r in records] == [
        ("purge_refused", "both_not_downloaded")
    ]


def test_signing_out_still_ends_the_session_and_removes_its_records_too(world):
    client = ready_client(world)
    both_downloaded(client)
    session = session_of(world)
    assert client.delete("/api/session", headers=csrf(client)).status_code == 204
    assert world.conversation._deliveries.get(session.session_id) == {}
    assert world.conversation._store.history(session.session_id) == (0, [])
