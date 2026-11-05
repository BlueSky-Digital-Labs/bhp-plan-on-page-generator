"""POST /api/email: the confirmed canvas and appendix, built as PDFs by the server and sent."""

import email
import logging
import uuid
from datetime import timedelta
from email import policy

import pytest

from app.api import mail as mail_routes
from app.mailer import MailerError
from app.mailer.limits import SendLimiter
from app.mailer.service import MailService
from tests.access.conftest import csrf
from tests.appendix.test_appendix_api import confirm, finish, finish_confirmed
from tests.interview.conftest import OPENING, browser
from tests.interview.test_interview_api import history, say, wire


class RecordingMailer:
    """TEST DOUBLE for SES: keeps what it was given, and can be told to fail or to do something
    while the send is in progress."""

    ready = True

    def __init__(self, fail: str | None = None, during=None) -> None:
        self.sent = []
        self.fail, self.during = fail, during

    def send(self, outgoing) -> None:
        if self.during:
            self.during()
        if self.fail:
            raise MailerError(self.fail)
        self.sent.append(outgoing)


def mailing(world, mailer=None, domains=(), limiter=None):
    mailer = mailer or RecordingMailer()
    world.mail = MailService(mailer, tuple(domains), limiter or SendLimiter(clock=world.clock))
    return mailer


def send(client, revision=None, to=("a@example.com",), message="", headers=None, **extra):
    revision = history(client)["revision"] if revision is None else revision
    body = {"revision": revision, "to": list(to), "message": message, **extra}
    return client.post(
        "/api/email", json=body, headers=csrf(client) if headers is None else headers
    )


def code(response) -> str:
    return response.json()["error"]["code"]


def ready_client(world, mailer=None, **kwargs):
    wire(world)
    mailer = mailing(world, mailer, **kwargs)
    client = browser(world)
    finish_confirmed(client)
    return client, mailer


# --- what is sent -----------------------------------------------------------------------------------


def test_both_documents_are_built_as_pdfs_and_sent_to_the_recipients_with_the_note(world):
    client, mailer = ready_client(world)
    revision = history(client)["revision"]
    response = send(client, to=("a@example.com", "b@example.org"), message="Please take a look.")
    assert response.status_code == 200 and response.json() == {"sent": 2}
    assert response.headers["cache-control"] == "no-store"
    (out,) = mailer.sent
    assert out.recipients == ("a@example.com", "b@example.org")
    assert "Please take a look." in out.body
    names = [a.filename for a in out.attachments]
    assert names[0].endswith(f"-canvas-r{revision}.pdf") and names[1].endswith(
        f"-appendix-r{revision}.pdf"
    )
    assert all(
        a.content.startswith(b"%PDF") and a.media_type == "application/pdf" for a in out.attachments
    )
    assert out.attachments[0].content != out.attachments[1].content
    assert out.subject.startswith("Plan on a Page: ")


def test_the_attached_documents_are_the_same_ones_the_download_buttons_give(world):
    client, mailer = ready_client(world)
    revision = history(client)["revision"]
    assert send(client).status_code == 200
    canvas = client.get("/api/canvas-file", params={"format": "pdf", "revision": revision})
    assert mailer.sent[0].attachments[0].content[:8] == canvas.content[:8]
    assert len(mailer.sent[0].attachments[0].content) == len(canvas.content)


def test_nothing_is_kept_by_sending(world):
    client, _ = ready_client(world)
    before = history(client)
    assert send(client).status_code == 200
    assert history(client) == before


def test_the_message_can_be_left_out(world):
    client, mailer = ready_client(world)
    assert send(client, message="").status_code == 200
    assert "Message from the sender" not in mailer.sent[0].body


def test_repeated_addresses_are_sent_once(world):
    client, mailer = ready_client(world)
    response = send(client, to=("a@example.com", " A@Example.com "))
    assert response.json() == {"sent": 1} and mailer.sent[0].recipients == ("a@example.com",)


# --- who it can go to -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "nope",
        "a@b",
        "Bob <b@example.com>",
        "a@example.com\nBcc: x@example.net",
        "",
        "тест@example.com",
    ],
)
def test_an_address_that_is_not_plain_is_refused_and_nothing_is_built(world, monkeypatch, bad):
    client, mailer = ready_client(world)
    built = []
    monkeypatch.setattr(mail_routes, "render_canvas_pdf", lambda c: built.append(c) or b"%PDF")
    response = send(client, to=(bad,))
    assert response.status_code == 422 and code(response) in (
        "invalid_recipient",
        "invalid_request",
    )
    assert bad.strip() == "" or bad not in response.text
    assert not mailer.sent and not built


def test_a_list_must_be_one_to_five_addresses(world):
    client, mailer = ready_client(world)
    assert send(client, to=()).status_code == 422
    assert send(client, to=[f"u{i}@example.com" for i in range(6)]).status_code == 422
    assert not mailer.sent
    assert send(client, to=[f"u{i}@example.com" for i in range(5)]).status_code == 200


def test_only_allowed_domains_receive_and_the_refusal_does_not_echo_the_address(world):
    client, mailer = ready_client(world, domains=("example.com",))
    response = send(client, to=("someone@example.com", "outsider@evil.example"))
    assert response.status_code == 422 and code(response) == "recipient_not_allowed"
    assert "evil" not in response.text and "outsider" not in response.text
    assert not mailer.sent
    assert send(client, to=("Someone@EXAMPLE.com",)).status_code == 200


def test_the_note_is_limited_and_unknown_fields_are_refused(world):
    client, mailer = ready_client(world)
    assert send(client, message="x" * 2001).status_code == 422
    assert send(client, message="x" * 2000).status_code == 200
    assert (
        send(client, **{"from": "boss@example.com"}).status_code == 422
    )  # the sender is not chosen here
    assert send(client, cc=["c@example.com"]).status_code == 422
    assert len(mailer.sent) == 1


# --- when it may be sent ----------------------------------------------------------------------------


def test_an_unconfirmed_canvas_is_never_emailed(world):
    wire(world)
    mailer = mailing(world)
    client = browser(world)
    finish(client)  # complete but not confirmed
    response = send(client)
    assert response.status_code == 409 and code(response) == "not_confirmed"
    assert not mailer.sent
    assert confirm(client).status_code == 200
    assert send(client).status_code == 200  # a refused attempt used up nothing


def test_an_incomplete_canvas_is_never_emailed(world):
    wire(world)
    mailer = mailing(world)
    client = browser(world)
    say(client, OPENING)
    response = send(client, revision=history(client)["revision"])
    assert response.status_code == 409 and code(response) == "canvas_not_ready"
    assert not mailer.sent


def test_only_the_current_revision_can_be_sent(world):
    client, mailer = ready_client(world)
    revision = history(client)["revision"]
    response = send(client, revision=revision - 1)
    assert response.status_code == 409 and code(response) == "revision_conflict"
    assert send(client, revision=revision + 1).status_code == 409
    assert not mailer.sent


def test_an_answer_after_confirming_means_the_old_confirmation_does_not_send(world):
    client, mailer = ready_client(world)
    say(client, "One more detail.")
    response = send(client)
    assert response.status_code == 409 and not mailer.sent


def test_an_answer_that_arrives_while_the_pdfs_are_built_stops_the_send(world, monkeypatch):
    client, mailer = ready_client(world)
    revision = history(client)["revision"]
    session_id = next(iter(world.store.sessions))
    real = mail_routes.render_canvas_pdf

    def render_and_race(canvas):
        content = real(canvas)
        world.conversation._store.submit(  # another tab answers mid-build
            session_id,
            uuid.uuid4().hex,
            "A late change.",
            revision,
            world.clock() + timedelta(hours=1),
            world.clock(),
        )
        return content

    monkeypatch.setattr(mail_routes, "render_canvas_pdf", render_and_race)
    response = send(client, revision=revision)
    assert response.status_code == 409 and code(response) == "revision_conflict"
    assert not mailer.sent


def test_one_session_cannot_email_another_sessions_canvas(world):
    client, mailer = ready_client(world)
    revision = history(client)["revision"]
    other = browser(world)
    assert send(other, revision=revision).status_code == 409
    assert not mailer.sent


# --- who may ask --------------------------------------------------------------------------------------


def test_it_needs_a_session_and_the_forgery_protections(world):
    client, mailer = ready_client(world)
    revision = history(client)["revision"]
    assert send(client, revision=revision, headers={}).status_code == 403
    assert (
        send(
            client, revision=revision, headers={**csrf(client), "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    token = csrf(client)
    client.cookies.clear()
    assert send(client, revision=revision, headers=token).status_code in (401, 403)
    assert not mailer.sent


def test_it_is_a_post_only_route(world):
    client, _ = ready_client(world)
    assert client.get("/api/email").status_code in (404, 405)  # nothing is read from here


# --- when email is not ready or fails ---------------------------------------------------------------


def test_without_email_set_up_it_says_so_at_once_and_builds_nothing(world, monkeypatch):
    wire(world)
    client = browser(world)  # no mail service: the default is "unavailable"
    finish_confirmed(client)
    built = []
    monkeypatch.setattr(mail_routes, "render_canvas_pdf", lambda c: built.append(c) or b"%PDF")
    response = send(client)
    assert response.status_code == 503 and code(response) == "email_unavailable"
    assert not built


@pytest.mark.parametrize(
    ("failure", "status", "expected"),
    [
        ("rejected", 502, "email_rejected"),
        ("failed", 502, "email_failed"),
        ("throttled", 503, "email_busy"),
        ("too_large", 413, "email_too_large"),
        ("unavailable", 503, "email_unavailable"),
        ("something-else", 502, "email_failed"),
    ],
)
def test_each_failure_has_fixed_guidance_and_does_not_use_up_the_allowance(
    world, failure, status, expected
):
    mailer = RecordingMailer(fail=failure)
    client, _ = ready_client(world, mailer, limiter=SendLimiter(per_session=1, clock=world.clock))
    response = send(client, message="SECRET-NOTE", to=("who@example.com",))
    assert response.status_code == status and code(response) == expected
    assert "SECRET-NOTE" not in response.text and "who@example.com" not in response.text
    mailer.fail = None
    assert send(client).status_code == 200  # still allowed: the failed attempt did not count


def test_a_session_has_a_limited_number_of_emails(world):
    client, mailer = ready_client(world, limiter=SendLimiter(per_session=2, clock=world.clock))
    assert send(client).status_code == 200 and send(client).status_code == 200
    response = send(client)
    assert response.status_code == 429 and code(response) == "email_limit"
    assert len(mailer.sent) == 2


def test_a_second_send_while_one_is_running_is_refused_not_doubled(world):
    seen = {}
    wire(world)

    def during():
        seen["inner"] = send(client)  # a second click arriving mid-send

    mailer = mailing(world, RecordingMailer(during=during))
    client = browser(world)  # after the mail service is set, so the app is built with it
    finish_confirmed(client)
    assert send(client).status_code == 200
    assert seen["inner"].status_code == 409 and code(seen["inner"]) == "send_in_progress"
    assert len(mailer.sent) == 1


def test_a_failed_render_gives_guidance_and_sends_nothing(world, monkeypatch):
    client, mailer = ready_client(world)

    def broken(canvas):
        raise RuntimeError("SECRET-IDEA-TEXT")

    monkeypatch.setattr(mail_routes, "render_canvas_pdf", broken)
    response = send(client)
    assert response.status_code == 500 and code(response) == "appendix_failed"
    assert "SECRET-IDEA-TEXT" not in response.text and not mailer.sent


def test_sending_is_logged_without_addresses_or_text(world):
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    client, _ = ready_client(world)
    logger = logging.getLogger("app.mailer")
    handler = Collect()
    logger.addHandler(handler)
    try:
        send(client, to=("private.person@example.com",), message="SECRET-NOTE")
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage() for r in records] == ["email_sent"]
    text = repr([r.__dict__ for r in records])
    assert "private" not in text and "SECRET-NOTE" not in text and "example.com" not in text


def test_the_raw_message_for_a_real_send_is_well_formed(world):
    from app.mailer import to_mime

    client, mailer = ready_client(world)
    send(client, to=("a@example.com",), message="Hello")
    message = email.message_from_bytes(
        to_mime("noreply@example.com", mailer.sent[0]), policy=policy.default
    )
    assert len(list(message.iter_attachments())) == 2 and message["To"] == "a@example.com"
