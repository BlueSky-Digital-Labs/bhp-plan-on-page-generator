"""What is sent, and the rules that keep it safe: recipients, headers, body and attachments."""

import email
from email import policy

import pytest

from app.mailer import (
    MAX_RECIPIENTS,
    Attachment,
    MailerError,
    OutgoingEmail,
    UnavailableMailer,
    clean_recipients,
    compose,
    to_mime,
)
from app.mailer.message import MAX_BYTES, single_line

PDF = Attachment("idea-canvas-r3.pdf", b"%PDF-1.7 canvas", "application/pdf")
APPENDIX = Attachment("idea-appendix-r3.pdf", b"%PDF-1.7 appendix", "application/pdf")


def parse(raw: bytes):
    return email.message_from_bytes(raw, policy=policy.default)


# --- recipients -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good", ["a@example.com", "first.last+tag@sub.example.co.uk", "A_B-C%d@Example.COM", "x@a-b.io"]
)
def test_plain_addresses_are_accepted(good):
    assert clean_recipients([good], ()) == (good,)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "plain",
        "a@b",  # no dot in the domain
        "a@@example.com",
        "a b@example.com",
        "Bob <bob@example.com>",  # a display name is not an address
        '"bob"@example.com',
        "a@example.com\nBcc: evil@example.net",  # header injection
        "a@example.com\r\nBcc: evil@example.net",
        "a@example.com,b@example.com",  # two in one
        "a@example.com;b@example.com",
        ".a@example.com",
        "a@-example.com.",
        "тест@example.com",  # not plain ASCII
        "a@exämple.com",
        "a@example.c",
        "a" * 70 + "@example.com",
        "a@" + "b" * 250 + ".com",
    ],
)
def test_anything_that_is_not_a_plain_address_is_refused(bad):
    with pytest.raises(ValueError, match="invalid_recipient"):
        clean_recipients([bad], ())


def test_addresses_are_trimmed_and_repeats_are_dropped_whatever_the_case():
    got = clean_recipients(["  a@example.com ", "A@EXAMPLE.COM", "b@example.com"], ())
    assert got == ("a@example.com", "b@example.com")


def test_there_must_be_between_one_and_the_maximum_recipients():
    with pytest.raises(ValueError, match="invalid_recipient"):
        clean_recipients([], ())
    many = [f"u{i}@example.com" for i in range(MAX_RECIPIENTS + 1)]
    with pytest.raises(ValueError, match="invalid_recipient"):
        clean_recipients(many, ())
    assert len(clean_recipients(many[:MAX_RECIPIENTS], ())) == MAX_RECIPIENTS


def test_only_allowed_domains_can_receive_when_a_list_is_set():
    allowed = ("example.com", "example.org")
    assert clean_recipients(["a@Example.COM", "b@example.org"], allowed)
    for outsider in (
        "a@example.net",
        "a@evil-example.com",
        "a@example.com.evil.io",
        "a@sub.example.com",
    ):
        with pytest.raises(ValueError, match="recipient_not_allowed"):
            clean_recipients(["ok@example.com", outsider], allowed)


# --- composing --------------------------------------------------------------------------------------


def test_the_email_names_the_canvas_lists_both_files_and_marks_the_senders_note():
    out = compose(
        ("a@example.com",), "Shift Notes Assistant", "Please read before Friday.", (PDF, APPENDIX)
    )
    assert out.subject == "Plan on a Page: Shift Notes Assistant"
    assert "“Shift Notes Assistant”" in out.body
    assert "idea-canvas-r3.pdf" in out.body and "idea-appendix-r3.pdf" in out.body
    assert "Message from the sender:\n\nPlease read before Friday." in out.body
    assert out.body.rstrip().endswith("Sent from Plan on a Page.")


def test_no_note_means_no_note_heading():
    assert "Message from the sender" not in compose(("a@example.com",), "X", "  \n ", (PDF,)).body


def test_the_note_is_plain_text_with_control_characters_removed():
    note = "Hi\x00 there\x07\r\nSecond line\twith tab <b>not bold</b>"
    body = compose(("a@example.com",), "X", note, (PDF,)).body
    assert "\x00" not in body and "\x07" not in body and "\r" not in body
    assert "Second line\twith tab <b>not bold</b>" in body  # markup is just text


@pytest.mark.parametrize(
    "name", ["Idea\r\nBcc: evil@example.net", "Idea\nSubject: other", " Idea ", "x" * 500]
)
def test_a_solution_name_cannot_break_the_headers(name):
    out = compose(("a@example.com",), name, "", (PDF,))
    assert "\n" not in out.subject and "\r" not in out.subject and len(out.subject) <= 120
    message = parse(to_mime("sender@example.com", out))
    assert message["Bcc"] is None
    assert message.get_all("Subject") == [out.subject]
    assert message.get_all("To") == ["a@example.com"]


def test_a_missing_name_still_gives_a_sensible_subject():
    assert compose(("a@example.com",), "  ", "", (PDF,)).subject == "Plan on a Page"


def test_single_line_flattens_and_bounds_text():
    assert single_line("a\r\nb\tc", 10) == "a b c"
    assert single_line("x" * 20, 5) == "xxxxx"


# --- the raw message ----------------------------------------------------------------------------------


def test_the_message_has_the_configured_sender_the_recipients_and_both_pdfs():
    out = compose(("a@example.com", "b@example.org"), "Idea", "Note", (PDF, APPENDIX))
    message = parse(to_mime("noreply@example.com", out))
    assert message["From"] == "noreply@example.com"
    assert message["To"] == "a@example.com, b@example.org"
    assert message["Cc"] is None and message["Bcc"] is None and message["Reply-To"] is None
    files = [
        (part.get_filename(), part.get_content_type(), part.get_content())
        for part in message.iter_attachments()
    ]
    assert files == [
        ("idea-canvas-r3.pdf", "application/pdf", b"%PDF-1.7 canvas"),
        ("idea-appendix-r3.pdf", "application/pdf", b"%PDF-1.7 appendix"),
    ]
    assert "Note" in message.get_body(("plain",)).get_content()


def test_non_ascii_text_is_carried_in_the_body_and_subject():
    out = compose(("a@example.com",), "Café ☕ plan", "Voilà — “quotes”", (PDF,))
    message = parse(to_mime("noreply@example.com", out))
    assert message["Subject"] == "Plan on a Page: Café ☕ plan"
    assert "Voilà — “quotes”" in message.get_body(("plain",)).get_content()


def test_attachments_over_the_cap_are_refused():
    big = Attachment("big.pdf", b"x" * (MAX_BYTES + 1), "application/pdf")
    with pytest.raises(MailerError) as caught:
        to_mime("noreply@example.com", OutgoingEmail(("a@example.com",), "s", "b", (big,)))
    assert caught.value.code == "too_large"


def test_the_unavailable_mailer_never_sends_and_says_so():
    mailer = UnavailableMailer()
    assert mailer.ready is False
    with pytest.raises(MailerError) as caught:
        mailer.send(OutgoingEmail(("a@example.com",), "s", "b", ()))
    assert caught.value.code == "unavailable"


def test_the_configured_app_name_is_used_in_the_subject_and_body():
    out = compose(("a@example.com",), "Idea", "", (PDF,), "Integrated Operations Plan")
    assert out.subject == "Integrated Operations Plan: Idea"
    assert "The canvas for “Idea” from Integrated Operations Plan is attached" in out.body
    assert out.body.rstrip().endswith("Sent from Integrated Operations Plan.")
    assert compose(("a@example.com",), " ", "", (PDF,), "Ops").subject == "Ops"
