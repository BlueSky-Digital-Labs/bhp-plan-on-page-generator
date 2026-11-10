"""The appendix download, through the real endpoints, and who may get it and when."""

import io
import json
import uuid
import zipfile
from datetime import timedelta

import pytest
from pypdf import PdfReader

from tests.access.conftest import csrf
from tests.interview.conftest import OPENING, FakeExtractor, browser
from tests.interview.test_interview_api import answer_for, history, say, wire

FORMATS = {"pdf": "application/pdf", "json": "application/json"}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def finish(client):
    """Drive a conversation to a canvas the server calls ready."""
    reply, data = say(client, OPENING)
    for _ in range(40):
        if data["canvas"]["ready_for_review"]:
            return data
        reply, data = say(client, answer_for(reply))
    raise AssertionError("did not finish")


def amend_behind_the_lock(world, client, text="One more detail."):
    """An answer that reaches the store anyway (a late write from another tab): the API itself refuses
    answers once the canvas is confirmed, but everything downstream must still cope with a changed
    revision."""
    revision = history(client)["revision"]
    session_id = next(iter(world.store.sessions))
    world.conversation._store.submit(
        session_id,
        uuid.uuid4().hex,
        text,
        revision,
        world.clock() + timedelta(hours=1),
        world.clock(),
    )


def appendix(client, fmt, revision=None):
    revision = history(client)["revision"] if revision is None else revision
    return client.get("/api/appendix", params={"format": fmt, "revision": revision})


def confirm(client, revision=None):
    """The user confirms the revision they read back (the current one unless told otherwise)."""
    revision = history(client)["revision"] if revision is None else revision
    return client.post("/api/canvas/confirm", json={"revision": revision}, headers=csrf(client))


def finish_confirmed(client):
    data = finish(client)
    assert confirm(client).status_code == 200
    return data


@pytest.mark.parametrize("fmt", ["pdf", "docx", "json"])
def test_with_nothing_answered_there_is_nothing_to_download(world, fmt):
    wire(world)
    client = browser(world)
    response = appendix(client, fmt)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_download"


@pytest.mark.parametrize("fmt", ["pdf", "docx", "json"])
def test_an_unfinished_canvas_downloads_as_a_marked_draft_to_carry_on_later(world, fmt):
    wire(world)
    client = browser(world)
    say(client, OPENING)
    assert history(client)["canvas"]["ready_for_review"] is False
    response = appendix(client, fmt)
    assert response.status_code == 200
    assert "-appendix-draft-r" in response.headers["content-disposition"]
    if fmt == "json":
        assert response.json()["draft"] is True
    elif fmt == "pdf":
        text = " ".join(
            " ".join(
                p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages
            ).split()
        )
        assert "UNFINISHED DRAFT" in text and "attach this file" in text
    else:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            assert b"UNFINISHED DRAFT" in z.read("word/document.xml")


def test_a_finished_canvas_is_not_marked_as_a_draft(world):
    wire(world)
    client = browser(world)
    finish(client)
    response = appendix(client, "json")
    assert response.json()["draft"] is False
    assert "-draft" not in response.headers["content-disposition"]


def test_without_an_interviewer_there_is_nothing_to_export(world):
    response = appendix(browser(world), "json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "canvas_not_ready"


def test_the_appendix_needs_a_session(world):
    wire(world)
    client = browser(world)
    finish_confirmed(client)
    revision = history(client)["revision"]
    client.cookies.clear()
    assert appendix(client, "json", revision).status_code == 401


def test_an_unknown_format_is_refused(world):
    wire(world)
    client = browser(world)
    finish(client)
    assert 400 <= appendix(client, "exe").status_code < 500
    assert 400 <= client.get("/api/appendix").status_code < 500


def test_a_ready_canvas_downloads_in_all_three_formats_as_attachments(world):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    for fmt, media in {**FORMATS, "docx": DOCX}.items():
        response = appendix(client, fmt)
        assert response.status_code == 200, fmt
        assert response.headers["content-type"].startswith(media)
        assert response.headers["content-disposition"] == (
            f'attachment; filename="shift-notes-assistant-appendix-r{data["revision"]}.{fmt}"'
        )
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-appendix-revision"] == str(data["revision"])
    assert appendix(client, "pdf").content.startswith(b"%PDF-")
    assert zipfile.is_zipfile(io.BytesIO(appendix(client, "docx").content))


def test_the_download_matches_the_stored_conversation_and_draft_exactly(world):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    exported = json.loads(appendix(client, "json").content)
    stored = [(t["role"], t["text"]) for t in data["turns"] if t["status"] == "complete"]
    assert [(t["speaker"], t["text"]) for t in exported["conversation"]] == stored
    assert exported["source_revision"] == data["revision"]
    answers = {a["id"]: a["value"] for s in exported["sections"] for a in s["answers"]}
    for field, value in data["canvas"]["fields"].items():
        assert answers[field] == value  # the full answers, not a summary
    assert [t["turn"] for t in exported["conversation"]] == list(range(1, len(stored) + 1))


def test_the_pdf_carries_the_same_record(world):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    text = "".join(
        p.extract_text() for p in PdfReader(io.BytesIO(appendix(client, "pdf").content)).pages
    )
    flat = "".join(text.split())
    for t in data["turns"]:
        assert "".join(t["text"].split()) in flat


def test_a_new_answer_takes_the_canvas_out_of_ready_so_the_next_download_is_a_draft(world):
    wire(world)
    client = browser(world)
    finish(client)
    assert appendix(client, "json").json()["draft"] is False
    # An answer that only opens a question (which part to change?) leaves the canvas unfinished.
    # A request that names a new value is applied at once and keeps it ready; that is covered in
    # tests/interview. Here the model finds nothing to record, so the assistant asks which part.
    world.conversation._producer._interviewer._extractor = FakeExtractor()
    say(client, "Can you fix something?")
    assert history(client)["canvas"]["ready_for_review"] is False
    assert appendix(client, "json").json()["draft"] is True


def test_one_session_never_receives_anothers_appendix(world):
    wire(world)
    owner, other = browser(world), browser(world)
    finish(owner)
    assert appendix(other, "json").status_code == 409  # their own, unfinished, canvas
    assert history(other)["turns"] == []


def test_a_conversation_that_changes_while_it_is_copied_is_refused_not_mixed(world, monkeypatch):
    wire(world)
    client = browser(world)
    finish(client)
    store = world.conversation._store
    real = store.history
    calls = iter(range(1000))
    monkeypatch.setattr(store, "history", lambda sid: (next(calls), real(sid)[1]))
    response = appendix(client, "json")
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "conversation_changed"
    )


def test_downloading_logs_the_event_without_any_content(world):
    import logging

    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    wire(world)
    client = browser(world)
    finish_confirmed(client)
    logger = logging.getLogger("app.appendix")
    handler = Collect()
    logger.addHandler(handler)
    try:
        appendix(client, "json")
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage() for r in records] == ["appendix_downloaded"]
    assert OPENING not in repr([r.__dict__ for r in records])


def test_ending_the_session_removes_what_could_be_exported(world):
    wire(world)
    client = browser(world)
    finish_confirmed(client)
    revision = history(client)["revision"]
    assert client.delete("/api/session", headers=csrf(client)).status_code == 204
    assert appendix(client, "json", revision).status_code == 401
