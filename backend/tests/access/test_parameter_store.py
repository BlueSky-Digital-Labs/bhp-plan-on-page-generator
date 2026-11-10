"""The two access secrets from Systems Manager Parameter Store (SecureString), on moto."""

import pytest
from moto import mock_aws
from pydantic import ValidationError

from app.access.errors import DependencyUnavailable
from app.access.secrets import ParameterStoreProvider, SecretsManagerProvider
from app.access.wiring import build_access_service
from app.config import AppEnv, Settings, failed_fields

HASH, KEY = "/plan-test/access-code-hash", "/plan-test/cookie-signing-key"
SIGNING = "k" * 48


@pytest.fixture
def ssm(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        import boto3

        client = boto3.client("ssm", region_name="ap-southeast-2")
        client.put_parameter(Name=HASH, Value="$argon2id$stored", Type="SecureString")
        client.put_parameter(Name=KEY, Value=SIGNING, Type="SecureString")
        yield client


def test_the_hash_and_key_are_read_decrypted(ssm):
    provider = ParameterStoreProvider(ssm, HASH, KEY)
    assert provider.access_code_hash() == "$argon2id$stored"
    assert provider.cookie_signing_key() == SIGNING.encode()


def test_the_hash_is_read_every_time_so_a_new_code_takes_effect_at_once(ssm):
    provider = ParameterStoreProvider(ssm, HASH, KEY)
    assert provider.access_code_hash() == "$argon2id$stored"
    ssm.put_parameter(Name=HASH, Value="$argon2id$rotated", Type="SecureString", Overwrite=True)
    assert provider.access_code_hash() == "$argon2id$rotated"


def test_the_key_is_cached_briefly(ssm):
    now = [100.0]
    provider = ParameterStoreProvider(ssm, HASH, KEY, clock=lambda: now[0])
    assert provider.cookie_signing_key() == SIGNING.encode()
    ssm.put_parameter(Name=KEY, Value="n" * 48, Type="SecureString", Overwrite=True)
    assert provider.cookie_signing_key() == SIGNING.encode()  # still cached
    now[0] += 61
    assert provider.cookie_signing_key() == ("n" * 48).encode()


def test_a_short_signing_key_is_refused(ssm):
    ssm.put_parameter(Name=KEY, Value="too-short", Type="SecureString", Overwrite=True)
    with pytest.raises(DependencyUnavailable):
        ParameterStoreProvider(ssm, HASH, KEY).cookie_signing_key()


def test_a_missing_parameter_fails_closed_without_provider_detail(ssm):
    provider = ParameterStoreProvider(ssm, "/plan-test/not-there", KEY)
    with pytest.raises(DependencyUnavailable) as caught:
        provider.access_code_hash()
    assert "ParameterNotFound" not in repr(caught.value) and "not-there" not in repr(caught.value)


def test_both_backends_share_one_implementation_of_the_caching_and_checks():
    from app.access.secrets import _StoredSecrets

    assert issubclass(ParameterStoreProvider, _StoredSecrets)
    assert issubclass(SecretsManagerProvider, _StoredSecrets)


# --- configuration and wiring --------------------------------------------------------------------


def settings(tmp_path, **kwargs) -> Settings:
    (tmp_path / "index.html").write_text("<!doctype html>")
    return Settings(
        app_env=AppEnv.DEPLOYED,
        static_dir=tmp_path,
        sessions_table_name="plan-test-sessions",
        **kwargs,
    )


def test_parameter_names_must_start_with_a_slash_for_that_backend(tmp_path):
    with pytest.raises(ValidationError) as caught:
        settings(
            tmp_path,
            secrets_backend="parameterstore",
            access_code_hash_secret_id="plan-test/access-code-hash",
            cookie_signing_key_secret_id=KEY,
        )
    assert failed_fields(caught.value) == "access_code_hash_secret_id"


def test_secrets_manager_names_are_unchanged_by_default(tmp_path):
    s = settings(
        tmp_path,
        access_code_hash_secret_id="plan-test/access-code-hash",
        cookie_signing_key_secret_id="plan-test/cookie-signing-key",
    )
    assert s.secrets_backend == "secretsmanager"


def test_an_unknown_backend_is_refused(tmp_path):
    with pytest.raises(ValidationError) as caught:
        settings(tmp_path, secrets_backend="vault")
    assert "secrets_backend" in failed_fields(caught.value)


@pytest.mark.parametrize(
    ("backend", "names", "service", "provider"),
    [
        ("parameterstore", (HASH, KEY), "ssm", ParameterStoreProvider),
        (
            "secretsmanager",
            ("plan-test/h", "plan-test/k"),
            "secretsmanager",
            SecretsManagerProvider,
        ),
    ],
)
def test_the_deployed_app_uses_the_chosen_backend(
    tmp_path, monkeypatch, backend, names, service, provider
):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    s = settings(
        tmp_path,
        secrets_backend=backend,
        access_code_hash_secret_id=names[0],
        cookie_signing_key_secret_id=names[1],
    )
    built = build_access_service(s)
    assert type(built._secrets) is provider
    assert built._secrets._client.meta.service_model.service_name == service
