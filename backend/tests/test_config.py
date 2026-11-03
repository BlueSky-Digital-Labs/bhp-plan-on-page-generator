import pytest

from app.config import AppEnv, ConfigError, Secrets, Settings, get_secrets, get_settings
from app.main import create_app


def test_missing_app_env_fails_startup():
    with pytest.raises(ConfigError, match="app_env"):
        get_settings()


def test_invalid_value_error_names_field_but_not_value(monkeypatch):
    monkeypatch.setenv("APP_ENV", "super-secret-value")
    with pytest.raises(ConfigError) as excinfo:
        get_settings()
    assert "app_env" in str(excinfo.value)
    assert "super-secret-value" not in str(excinfo.value)


def test_deployed_requires_compiled_frontend(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "deployed")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="static_dir"):
        get_settings()


def test_deployed_starts_with_frontend_and_hides_docs(monkeypatch, static_dir):
    monkeypatch.setenv("APP_ENV", "deployed")
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    app = create_app()
    assert app.docs_url is None and app.openapi_url is None


def test_local_default_region_is_sydney(static_dir):
    assert Settings(app_env=AppEnv.LOCAL, static_dir=static_dir).aws_region == "ap-southeast-2"


def test_secrets_are_masked_and_optional(monkeypatch):
    assert get_secrets().cookie_signing_key is None
    monkeypatch.setenv("COOKIE_SIGNING_KEY", "do-not-print-me")
    secrets = Secrets()
    assert "do-not-print-me" not in repr(secrets)
    assert "do-not-print-me" not in str(secrets)
