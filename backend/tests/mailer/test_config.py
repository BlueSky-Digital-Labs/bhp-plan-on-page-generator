"""Email settings: what must be configured before real sending can start."""

import pytest
from pydantic import ValidationError

from app.config import AppEnv, ConfigError, Settings, failed_fields
from app.mailer import SesMailer, UnavailableMailer
from app.mailer.settings import (
    NoSubmitSettings,
    ParameterStoreSubmitSettings,
    StaticSubmitSettings,
)
from app.main import DiscardingMailer, create_app


def settings(static_dir, **kwargs) -> Settings:
    return Settings(app_env=AppEnv.LOCAL, static_dir=static_dir, **kwargs)


@pytest.fixture
def static_dir(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    return tmp_path


def test_email_is_off_by_default_and_nothing_is_sent(static_dir):
    s = settings(static_dir)
    assert s.email_delivery == "unavailable" and s.allowed_email_domains == ()
    app = create_app(s)
    assert isinstance(app.state.mail.mailer, UnavailableMailer) and not app.state.mail.mailer.ready


def test_ses_needs_a_plain_sender_address(static_dir):
    for bad in (None, "", "not an address", "Name <a@example.com>"):
        with pytest.raises(ValidationError) as caught:
            settings(
                static_dir,
                email_delivery="ses",
                email_allowed_domains="example.com",
                email_sender_address=bad,
            )
        assert "email_sender_address" in failed_fields(caught.value)


def test_ses_needs_the_allowed_recipient_domains(static_dir):
    for bad in ("", " ", ",", " , "):
        with pytest.raises(ValidationError) as caught:
            settings(
                static_dir,
                email_delivery="ses",
                email_sender_address="noreply@example.com",
                email_allowed_domains=bad,
            )
        assert "email_allowed_domains" in failed_fields(caught.value)


def test_the_discarding_test_mailer_is_refused_when_deployed(static_dir, monkeypatch):
    monkeypatch.setenv("SESSIONS_TABLE_NAME", "t")
    monkeypatch.setenv("ACCESS_CODE_HASH_SECRET_ID", "a")
    monkeypatch.setenv("COOKIE_SIGNING_KEY_SECRET_ID", "c")
    with pytest.raises(ValidationError) as caught:
        Settings(app_env=AppEnv.DEPLOYED, static_dir=static_dir, email_delivery="test")
    assert "email_delivery" in failed_fields(caught.value)
    assert isinstance(
        create_app(settings(static_dir, email_delivery="test")).state.mail.mailer, DiscardingMailer
    )


def test_allowed_domains_are_read_loosely_and_compared_in_lower_case(static_dir):
    s = settings(static_dir, email_allowed_domains=" Example.COM, @example.org ,,example.com")
    assert s.allowed_email_domains == ("example.com", "example.org")


def test_ses_delivery_builds_a_ses_mailer_in_the_configured_region(static_dir, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    s = settings(
        static_dir,
        email_delivery="ses",
        email_sender_address=" noreply@example.com ",
        email_allowed_domains="example.com",
        email_recipient=" Team@Example.com ",
        ses_region="us-east-1",
    )
    mail = create_app(s).state.mail
    assert isinstance(mail.mailer, SesMailer) and mail.mailer.ready
    assert mail.mailer._sender == "noreply@example.com"  # noqa: SLF001
    assert mail.mailer._client.meta.region_name == "us-east-1"  # noqa: SLF001
    assert isinstance(mail.submit, StaticSubmitSettings)
    assert mail.submit.current().recipient == "Team@Example.com"


def test_a_startup_error_names_the_setting_but_not_its_value(static_dir, monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("EMAIL_DELIVERY", "ses")
    monkeypatch.setenv("EMAIL_SENDER_ADDRESS", "secret-looking value")
    monkeypatch.setenv("EMAIL_ALLOWED_DOMAINS", "example.com")
    from app.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(ConfigError) as caught:
        get_settings()
    get_settings.cache_clear()
    assert "email_sender_address" in str(caught.value) and "secret-looking" not in str(caught.value)


SES = dict(
    email_delivery="ses",
    email_sender_address="noreply@example.com",
    email_allowed_domains="example.com",
)


def test_ses_needs_a_recipient_or_the_parameter_that_holds_it(static_dir):
    with pytest.raises(ValidationError) as caught:
        settings(static_dir, **SES)
    assert "email_recipient_parameter" in failed_fields(caught.value)
    assert settings(static_dir, email_recipient="a@example.com", **SES)
    assert settings(static_dir, email_recipient_parameter="/plan-test/email-recipient", **SES)


@pytest.mark.parametrize("bad", ["nope", "Name <a@example.com>", "a@b"])
def test_a_fixed_recipient_must_be_a_plain_address(static_dir, bad):
    with pytest.raises(ValidationError) as caught:
        settings(static_dir, email_recipient=bad, **SES)
    assert "email_recipient" in failed_fields(caught.value)


def test_parameter_names_must_start_with_a_slash(static_dir):
    for field in ("email_recipient_parameter", "email_body_parameter"):
        with pytest.raises(ValidationError) as caught:
            settings(static_dir, **{**SES, "email_recipient": "a@example.com", field: "no-slash"})
        assert field in failed_fields(caught.value)


def test_a_fixed_recipient_outside_the_allowed_domains_stops_the_app_starting(static_dir):
    s = settings(static_dir, email_recipient="someone@evil.example", **SES)
    with pytest.raises(ValueError, match="recipient_not_allowed"):
        create_app(s)


def test_the_recipient_is_read_from_parameter_store_when_a_parameter_is_named(
    static_dir, monkeypatch
):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    s = settings(
        static_dir,
        email_recipient_parameter="/plan-test/email-recipient",
        email_body_parameter="/plan-test/email-body",
        **SES,
    )
    mail = create_app(s).state.mail
    assert isinstance(mail.submit, ParameterStoreSubmitSettings)


def test_nothing_configured_means_submissions_are_unavailable(static_dir):
    assert isinstance(create_app(settings(static_dir)).state.mail.submit, NoSubmitSettings)
    assert create_app(settings(static_dir)).state.mail.submit.current() is None


def test_the_discarding_mailer_has_a_recipient_without_any_setting(static_dir):
    mail = create_app(settings(static_dir, email_delivery="test")).state.mail
    assert mail.submit.current().recipient == "submissions@example.com"
