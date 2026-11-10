"""Where a submission goes and what it says, read at run time, against a stubbed Systems Manager."""

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.mailer import DEFAULT_BODY
from app.mailer.settings import (
    NoSubmitSettings,
    ParameterStoreSubmitSettings,
    StaticSubmitSettings,
)

RECIPIENT = "/plan-test/email-recipient"
BODY = "/plan-test/email-body"


class FakeSsm:
    """TEST DOUBLE for Systems Manager: serves whatever `values` holds, or fails when told to."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.fail: Exception | None = None
        self.calls = 0

    def get_parameters(self, Names):  # noqa: N803 - the SDK's spelling
        self.calls += 1
        if self.fail:
            raise self.fail
        return {
            "Parameters": [{"Name": n, "Value": self.values[n]} for n in Names if n in self.values]
        }


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def source(ssm, domains=("example.com",), body_name=BODY, clock=None):
    return ParameterStoreSubmitSettings(ssm, RECIPIENT, body_name, domains, clock=clock or Clock())


def test_nothing_configured_gives_nothing_to_send_to():
    assert NoSubmitSettings().current() is None


def test_a_fixed_recipient_is_checked_once_and_the_text_defaults():
    got = StaticSubmitSettings("Team@Example.com", "", ("example.com",)).current()
    assert (got.recipient, got.body) == ("Team@Example.com", DEFAULT_BODY)
    with pytest.raises(ValueError, match="recipient_not_allowed"):
        StaticSubmitSettings("x@evil.example", "", ("example.com",))


def test_the_recipient_and_text_come_from_the_parameters():
    ssm = FakeSsm({RECIPIENT: " team@example.com ", BODY: "Hello\nteam"})
    got = source(ssm).current()
    assert (got.recipient, got.body) == ("team@example.com", "Hello\nteam")


def test_the_text_is_optional_and_falls_back_to_the_default():
    assert source(FakeSsm({RECIPIENT: "team@example.com"})).current().body == DEFAULT_BODY
    assert source(FakeSsm({RECIPIENT: "team@example.com"}), body_name=None).current().body == (
        DEFAULT_BODY
    )
    assert source(FakeSsm({RECIPIENT: "team@example.com", BODY: "  "})).current().body == (
        DEFAULT_BODY
    )


@pytest.mark.parametrize(
    "bad",
    ["", "nope", "Bob <b@example.com>", "a@example.com, b@example.com", "x@evil.example"],
)
def test_a_missing_or_unacceptable_recipient_means_nothing_is_sent(bad, caplog):
    caplog.set_level("ERROR", logger="app.mailer")
    assert source(FakeSsm({RECIPIENT: bad})).current() is None
    assert "email_recipient_invalid" in [r.getMessage() for r in caplog.records]
    assert bad.strip() == "" or bad not in repr([r.__dict__ for r in caplog.records])


def test_a_change_is_picked_up_after_a_minute_without_a_restart():
    ssm, clock = FakeSsm({RECIPIENT: "old@example.com"}), Clock()
    s = source(ssm, clock=clock)
    assert s.current().recipient == "old@example.com"
    ssm.values[RECIPIENT] = "new@example.com"
    clock.now += 30
    assert s.current().recipient == "old@example.com" and ssm.calls == 1  # still cached
    clock.now += 31
    assert s.current().recipient == "new@example.com" and ssm.calls == 2


def test_a_bad_edit_stops_sending_at_the_next_read_and_a_fix_restores_it():
    ssm, clock = FakeSsm({RECIPIENT: "team@example.com"}), Clock()
    s = source(ssm, clock=clock)
    assert s.current() is not None
    ssm.values[RECIPIENT] = "typo"
    clock.now += 61
    assert s.current() is None
    ssm.values[RECIPIENT] = "team@example.com"
    clock.now += 61
    assert s.current().recipient == "team@example.com"


@pytest.mark.parametrize(
    "failure",
    [
        ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "SECRET"}}, "GetParameters"
        ),
        EndpointConnectionError(endpoint_url="https://ssm.example"),
    ],
)
def test_a_failed_read_keeps_the_last_good_value_and_retries_soon(failure, caplog):
    caplog.set_level("ERROR", logger="app.mailer")
    ssm, clock = FakeSsm({RECIPIENT: "team@example.com"}), Clock()
    s = source(ssm, clock=clock)
    assert s.current().recipient == "team@example.com"
    ssm.fail = failure
    clock.now += 61
    assert s.current().recipient == "team@example.com"  # kept
    calls = ssm.calls
    clock.now += 5
    s.current()
    assert ssm.calls == calls  # not hammering
    clock.now += 6
    ssm.fail = None
    ssm.values[RECIPIENT] = "other@example.com"
    assert s.current().recipient == "other@example.com"
    assert "SECRET" not in repr([r.__dict__ for r in caplog.records])


def test_without_a_good_value_yet_a_failed_read_means_unavailable():
    ssm = FakeSsm({RECIPIENT: "team@example.com"})
    ssm.fail = EndpointConnectionError(endpoint_url="https://ssm.example")
    assert source(ssm).current() is None


def test_nothing_is_logged_about_who_it_goes_to(caplog):
    caplog.set_level("INFO", logger="app.mailer")
    source(FakeSsm({RECIPIENT: "private.person@example.com", BODY: "SECRET-TEXT"})).current()
    assert "private" not in repr([r.__dict__ for r in caplog.records])
