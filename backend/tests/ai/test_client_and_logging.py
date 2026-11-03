import logging

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


def test_sdk_loggers_stay_at_warning_even_when_app_is_at_debug():
    # botocore logs request/response parameters at DEBUG, which would capture prompts.
    for name in ("boto3", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    for name in ("boto3", "botocore", "urllib3"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING
