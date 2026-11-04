"""The SES sender, against a stubbed SESv2 client: no AWS, no network."""

import email
import logging
from email import policy

import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber

from app.mailer import (
    Attachment,
    MailerError,
    OutgoingEmail,
    SesMailer,
    build_ses_client,
)

EMAIL = OutgoingEmail(
    ("a@example.com", "b@example.org"),
    "Plan on a Page: Idea",
    "Body text\n",
    (Attachment("idea-canvas-r3.pdf", b"%PDF-canvas", "application/pdf"),),
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")  # fake credentials so a client builds offline
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    return build_ses_client("ap-southeast-2")


def test_the_client_is_in_the_given_region_and_never_retries_a_send(client):
    assert client.meta.region_name == "ap-southeast-2"
    assert client.meta.service_model.service_name == "sesv2"
    assert client.meta.config.retries["total_max_attempts"] == 1  # a retry could email twice
    assert client.meta.config.connect_timeout == 5 and client.meta.config.read_timeout == 20


def test_the_request_sent_to_ses_is_exactly_what_was_composed(client):
    seen = {}
    client.meta.events.register(
        "provide-client-params.sesv2.SendEmail", lambda params, **_: seen.update(params)
    )
    with Stubber(client) as stub:
        stub.add_response("send_email", {"MessageId": "m-1"})
        SesMailer(client, "noreply@example.com", "reports").send(EMAIL)
    assert seen["FromEmailAddress"] == "noreply@example.com"
    assert seen["Destination"] == {"ToAddresses": ["a@example.com", "b@example.org"]}
    assert seen["ConfigurationSetName"] == "reports"
    raw = email.message_from_bytes(seen["Content"]["Raw"]["Data"], policy=policy.default)
    assert raw["From"] == "noreply@example.com" and raw["Subject"] == "Plan on a Page: Idea"
    assert [p.get_filename() for p in raw.iter_attachments()] == ["idea-canvas-r3.pdf"]


def test_no_configuration_set_is_sent_unless_one_is_configured(client):
    seen = {}
    client.meta.events.register(
        "provide-client-params.sesv2.SendEmail", lambda params, **_: seen.update(params)
    )
    with Stubber(client) as stub:
        stub.add_response("send_email", {"MessageId": "m-1"})
        SesMailer(client, "noreply@example.com").send(EMAIL)
    assert "ConfigurationSetName" not in seen


@pytest.mark.parametrize(
    ("provider_code", "ours"),
    [
        ("MessageRejected", "rejected"),  # e.g. a recipient that is not verified in the sandbox
        ("MailFromDomainNotVerifiedException", "rejected"),
        ("AccountSuspendedException", "rejected"),
        ("SendingPausedException", "rejected"),
        ("NotFoundException", "rejected"),
        ("BadRequestException", "rejected"),
        ("TooManyRequestsException", "throttled"),
        ("LimitExceededException", "throttled"),
        ("SomethingNew", "failed"),
        ("", "failed"),
    ],
)
def test_provider_errors_become_one_of_our_fixed_outcomes(client, provider_code, ours):
    with Stubber(client) as stub:
        stub.add_client_error(
            "send_email", service_error_code=provider_code, service_message="SECRET-NOTE"
        )
        with pytest.raises(MailerError) as caught:
            SesMailer(client, "noreply@example.com").send(EMAIL)
    assert caught.value.code == ours
    assert "SECRET-NOTE" not in str(caught.value) and caught.value.__cause__ is None


def test_a_network_failure_is_a_failed_send(client, monkeypatch):
    monkeypatch.setattr(
        client,
        "send_email",
        lambda **_: (_ for _ in ()).throw(EndpointConnectionError(endpoint_url="https://x")),
    )
    with pytest.raises(MailerError) as caught:
        SesMailer(client, "noreply@example.com").send(EMAIL)
    assert caught.value.code == "failed"


def test_failures_are_logged_without_addresses_or_text(client):
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("app.mailer")
    handler = Collect()
    logger.addHandler(handler)
    try:
        with Stubber(client) as stub:
            stub.add_client_error(
                "send_email",
                service_error_code="MessageRejected",
                service_message="a@example.com SECRET-NOTE",
            )
            with pytest.raises(MailerError):
                SesMailer(client, "noreply@example.com").send(EMAIL)
    finally:
        logger.removeHandler(handler)
    assert [r.getMessage() for r in records] == ["email_send_failed"]
    text = repr([r.__dict__ for r in records])
    assert "example.com" not in text and "SECRET-NOTE" not in text and "Idea" not in text
    assert records[0].failure_code == "MessageRejected"
