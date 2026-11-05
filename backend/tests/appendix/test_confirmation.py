"""The read-back confirmation: documents exist only for the revision the user confirmed."""

import json
import uuid
from datetime import timedelta

import pytest

from app.api import canvas as canvas_routes
from tests.access.conftest import csrf
from tests.appendix.test_appendix_api import appendix, confirm, finish, finish_confirmed
from tests.appendix.test_canvas_pptx import canvas_file
from tests.interview.conftest import OPENING, browser
from tests.interview.test_interview_api import answer_for, history, say, submit, wire

DOCUMENTS = [
    ("canvas pdf", lambda c, r=None: canvas_file(c, "pdf", r)),
    ("canvas pptx", lambda c, r=None: canvas_file(c, "pptx", r)),
    ("appendix pdf", lambda c, r=None: appendix(c, "pdf", r)),
    ("appendix docx", lambda c, r=None: appendix(c, "docx", r)),
    ("appendix json", lambda c, r=None: appendix(c, "json", r)),
]
IDS = [name for name, _ in DOCUMENTS]


def error_code(response) -> str:
    return response.json()["error"]["code"]


# --- confirming --------------------------------------------------------------------------------------


def test_confirming_records_the_revision_and_returns_the_refreshed_review(world):
    wire(world)
    client = browser(world)
    data = finish(client)
    assert data["confirmed_revision"] is None  # reading is not confirming
    response = confirm(client)
    assert response.status_code == 200
    review = response.json()
    assert review["confirmed_revision"] == review["revision"] == data["revision"]
    assert (
        review["canvas"]["ready_for_review"]
        and review["canvas"]["fields"]["priority"] == "Must-have"
    )
    assert history(client)["confirmed_revision"] == data["revision"]


def test_an_incomplete_canvas_cannot_be_confirmed(world):
    wire(world)
    client = browser(world)
    say(client, OPENING)
    response = confirm(client)
    assert response.status_code == 422 and error_code(response) == "canvas_incomplete"
    assert history(client)["confirmed_revision"] is None


def test_a_reply_still_being_written_blocks_confirming(world):
    wire(world)
    client = browser(world)
    finish(client)
    revision = history(client)["revision"]
    submit(
        client, "One more detail.", revision, submission_id=uuid.uuid4().hex
    )  # reply not streamed
    response = confirm(client, revision + 1)
    assert response.status_code == 409 and error_code(response) == "turn_in_progress"


@pytest.mark.parametrize("offset", [-1, 1, 100])
def test_only_the_revision_that_was_shown_can_be_confirmed(world, offset):
    wire(world)
    client = browser(world)
    data = finish(client)
    response = confirm(client, data["revision"] + offset)
    assert response.status_code == 409 and error_code(response) == "revision_conflict"
    assert history(client)["confirmed_revision"] is None


def test_confirming_needs_a_session_and_the_forgery_protections(world):
    wire(world)
    client = browser(world)
    data = finish(client)
    assert (
        client.post("/api/canvas/confirm", json={"revision": data["revision"]}).status_code == 403
    )
    assert (
        client.post(
            "/api/canvas/confirm",
            json={"revision": data["revision"]},
            headers={**csrf(client), "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.post("/api/canvas/confirm", json={"revision": 1}).status_code in (401, 403)


def test_the_body_takes_only_a_revision_so_nothing_else_such_as_a_priority_can_be_supplied(world):
    wire(world)
    client = browser(world)
    data = finish(client)
    extra = client.post(
        "/api/canvas/confirm",
        json={"revision": data["revision"], "priority": "Exploratory"},
        headers=csrf(client),
    )
    assert extra.status_code == 422
    assert (
        history(client)["canvas"]["fields"]["priority"] == "Must-have"
    )  # still the user's own choice
    assert history(client)["confirmed_revision"] is None


def test_confirmations_belong_to_one_session(world):
    wire(world)
    owner, other = browser(world), browser(world)
    finish_confirmed(owner)
    assert history(other)["confirmed_revision"] is None
    assert confirm(other).status_code == 422  # their own canvas is empty


# --- documents --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "get"), DOCUMENTS, ids=IDS)
def test_no_document_is_made_until_the_user_confirms(world, name, get):
    wire(world)
    client = browser(world)
    finish(client)
    response = get(client)
    assert response.status_code == 409 and error_code(response) == "not_confirmed"


@pytest.mark.parametrize(("name", "get"), DOCUMENTS, ids=IDS)
def test_a_document_for_any_other_revision_is_refused(world, name, get):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    for stale in (data["revision"] - 1, data["revision"] + 1):
        response = get(client, stale)
        assert response.status_code == 409 and error_code(response) == "revision_conflict"


@pytest.mark.parametrize(("name", "get"), DOCUMENTS, ids=IDS)
def test_every_document_downloads_once_the_revision_is_confirmed(world, name, get):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    response = get(client)
    assert response.status_code == 200 and response.content
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-appendix-revision"] == str(data["revision"])
    assert "attachment" in response.headers["content-disposition"]


def test_the_canvas_pdf_is_a_pdf_with_the_confirmed_answers(world):
    from io import BytesIO

    from pypdf import PdfReader

    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    response = canvas_file(client, "pdf")
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    pages = PdfReader(BytesIO(response.content)).pages
    assert len(pages) == 1
    text = " ".join(pages[0].extract_text().split())
    assert data["canvas"]["fields"]["solutionName"] in text


def test_a_later_answer_ends_the_confirmation_at_once_and_a_new_one_is_needed(world):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    assert canvas_file(client, "pdf").status_code == 200

    submit(
        client,
        "Actually, it should also cover night shifts.",
        data["revision"],
        submission_id=uuid.uuid4().hex,
    )
    now = history(client)
    assert (
        now["confirmed_revision"] == data["revision"] != now["revision"]
    )  # no longer the current one
    for _, get in DOCUMENTS:
        assert get(client).status_code == 409  # refused while the reply is pending and after

    # Finish the interview again; the user must read it back and confirm the new revision.
    turn_id = now["turns"][-1]["turn_id"]
    client.get(f"/api/conversation/turns/{turn_id}/stream")
    latest = history(client)
    reply = latest["turns"][-1]["text"]
    for _ in range(40):
        if latest["canvas"]["ready_for_review"]:
            break
        answer = "Use the new one." if "Which one should I use" in reply else answer_for(reply)
        reply, latest = say(client, answer)  # the amendment contradicted an earlier answer
    assert latest["confirmed_revision"] != latest["revision"]
    assert canvas_file(client, "pdf").status_code == 409
    assert confirm(client).status_code == 200
    assert canvas_file(client, "pdf").status_code == 200


def test_an_answer_that_arrives_while_the_file_is_being_built_stops_it_being_sent(
    world, monkeypatch
):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    session_id = next(iter(world.store.sessions))
    real_render, media = canvas_routes._FILES["pdf"]

    def render_and_race(canvas):
        content = real_render(canvas)
        world.conversation._store.submit(  # another tab answers mid-render
            session_id,
            uuid.uuid4().hex,
            "A late change.",
            data["revision"],
            world.clock() + timedelta(hours=1),
            world.clock(),
        )
        return content

    monkeypatch.setitem(canvas_routes._FILES, "pdf", (render_and_race, media))
    response = canvas_file(client, "pdf", data["revision"])
    assert response.status_code == 409 and error_code(response) == "revision_conflict"
    assert response.headers["content-type"].startswith("application/json")  # no file was sent


def test_a_failed_render_gives_guidance_and_no_file_and_changes_nothing(world, monkeypatch):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    real_render, media = canvas_routes._FILES["pdf"]

    def broken(canvas):
        raise RuntimeError("SECRET-IDEA-TEXT")

    monkeypatch.setitem(canvas_routes._FILES, "pdf", (broken, media))
    response = canvas_file(client, "pdf")
    assert response.status_code == 500 and error_code(response) == "appendix_failed"
    assert "SECRET-IDEA-TEXT" not in response.text
    assert history(client)["confirmed_revision"] == data["revision"]  # still confirmed, work intact
    monkeypatch.setitem(canvas_routes._FILES, "pdf", (real_render, media))
    assert canvas_file(client, "pdf").status_code == 200  # retry works


def test_one_session_cannot_download_another_sessions_confirmed_canvas(world):
    wire(world)
    owner, other = browser(world), browser(world)
    data = finish_confirmed(owner)
    for _, get in DOCUMENTS:
        response = get(other, data["revision"])
        assert (
            response.status_code == 409
            and json.dumps(data["canvas"]["fields"]) not in response.text
        )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Shift Notes Assistant", "shift-notes-assistant"),
        ("../../etc/passwd", "etc-passwd"),
        ('a/b\\c:d*e?"f<g>h|i', "a-b-c-d-e-f-g-h-i"),
        ("Café Ωmega Привет", "cafe-mega"),
        ("line\r\nbreak: x", "line-break-x"),
        ("", "plan-on-a-page"),
        ("   ", "plan-on-a-page"),
        ("Привет Ωmega", "mega"),
        ("Привет", "plan-on-a-page"),
        ("x" * 200, "x" * 40),
    ],
)
def test_file_names_are_short_readable_and_safe(name, expected):
    from app.canvas_export import safe_filename_stem

    assert safe_filename_stem(name) == expected
