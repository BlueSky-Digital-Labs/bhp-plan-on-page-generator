import logging
from pathlib import Path

import pytest
from botocore.session import Session as BotocoreSession

from app.ai.client import build_bedrock_runtime_client
from app.ai.config import BedrockConfig
from app.logging_config import configure_logging


def test_client_uses_configured_region_timeouts_and_bounded_retries(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")  # fake creds so the client can be built offline
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    config = BedrockConfig(
        region="ap-southeast-2",
        model_id="m:0",
        connect_timeout_seconds=2.0,
        read_timeout_seconds=30.0,
        max_attempts=2,
    )
    client = build_bedrock_runtime_client(config)
    assert client.meta.region_name == "ap-southeast-2"
    assert client.meta.service_model.service_name == "bedrock-runtime"
    assert client.meta.config.connect_timeout == 2.0
    assert client.meta.config.read_timeout == 30.0
    assert client.meta.config.retries["total_max_attempts"] == 2


class _RecordingSession:
    def __init__(self, profile_name=None):
        self.profile_name = profile_name

    def client(self, *args, **kwargs):
        return self


def _config():
    return BedrockConfig(
        region="ap-southeast-2",
        model_id="m:0",
        connect_timeout_seconds=2.0,
        read_timeout_seconds=30.0,
        max_attempts=2,
    )


def test_client_uses_the_named_profile_when_one_is_set(monkeypatch):
    monkeypatch.setattr("app.ai.client.boto3.Session", _RecordingSession)
    monkeypatch.setenv("BEDROCK_PROFILE", "rnd-bedrock")
    assert build_bedrock_runtime_client(_config()).profile_name == "rnd-bedrock"


@pytest.mark.parametrize("value", [None, ""])
def test_client_uses_the_default_chain_without_a_profile(monkeypatch, value):
    monkeypatch.setattr("app.ai.client.boto3.Session", _RecordingSession)
    monkeypatch.delenv("BEDROCK_PROFILE", raising=False)
    if value is not None:
        monkeypatch.setenv("BEDROCK_PROFILE", value)
    assert build_bedrock_runtime_client(_config()).profile_name is None


def test_the_shipped_aws_config_defines_the_profile_the_deployment_names(monkeypatch):
    monkeypatch.setenv("AWS_CONFIG_FILE", str(Path(__file__).resolve().parents[2] / "aws-config"))
    profile = BotocoreSession().full_config["profiles"]["rnd-bedrock"]
    assert profile["role_arn"].endswith(":role/bhp-plan-test-bedrock")
    assert profile["credential_source"] == "EcsContainer"


def test_sdk_loggers_stay_at_warning_even_when_app_is_at_debug():
    # botocore logs request/response parameters at DEBUG, which would capture prompts.
    for name in ("boto3", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    for name in ("boto3", "botocore", "urllib3"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
