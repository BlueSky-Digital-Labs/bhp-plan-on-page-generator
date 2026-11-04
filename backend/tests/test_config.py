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
    monkeypatch.setenv("SESSIONS_TABLE_NAME", "sessions")
    monkeypatch.setenv("ACCESS_CODE_HASH_SECRET_ID", "access-hash")
    monkeypatch.setenv("COOKIE_SIGNING_KEY_SECRET_ID", "cookie-key")
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


def test_deployed_requires_access_control_identifiers(monkeypatch, static_dir):
    monkeypatch.setenv("APP_ENV", "deployed")
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("SESSIONS_TABLE_NAME", "sessions")  # the two secret ids are missing
    with pytest.raises(ConfigError) as excinfo:
        get_settings()
    assert "access_code_hash_secret_id" in str(excinfo.value)
    assert "cookie_signing_key_secret_id" in str(excinfo.value)
    assert "sessions_table_name" not in str(excinfo.value)


def test_the_scripted_test_producer_is_refused_when_deployed(monkeypatch, static_dir):
    monkeypatch.setenv("APP_ENV", "deployed")
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("SESSIONS_TABLE_NAME", "sessions")
    monkeypatch.setenv("ACCESS_CODE_HASH_SECRET_ID", "access-hash")
    monkeypatch.setenv("COOKIE_SIGNING_KEY_SECRET_ID", "cookie-key")
    monkeypatch.setenv("CONVERSATION_PRODUCER", "test")
    with pytest.raises(ConfigError, match="conversation_producer"):
        get_settings()


def test_the_producer_defaults_to_unavailable_so_nothing_is_fabricated(static_dir):
    assert (
        Settings(app_env=AppEnv.LOCAL, static_dir=static_dir).conversation_producer == "unavailable"
    )
