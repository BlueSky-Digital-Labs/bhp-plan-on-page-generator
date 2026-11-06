import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws

from app import dynamo
from app.config import AppEnv, Settings, get_secrets, get_settings
from app.conversation.dynamo_store import DynamoConversationStore
from app.delivery.dynamo_store import DynamoDeliveryStore
from app.interview.dynamo_store import DynamoInterviewStore
from app.main import create_app


@pytest.fixture(autouse=True)
def _clean_config_cache(monkeypatch):
    for name in (
        "APP_ENV",
        "STATIC_DIR",
        "AWS_REGION",
        "LOG_LEVEL",
        "SESSIONS_TABLE_NAME",
        "ACCESS_CODE_HASH_SECRET_ID",
        "COOKIE_SIGNING_KEY_SECRET_ID",
        "ACCESS_CODE_HASH",
        "COOKIE_SIGNING_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in [n for n in os.environ if n.startswith("BEDROCK_")]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    get_secrets.cache_clear()
    yield
    get_settings.cache_clear()
    get_secrets.cache_clear()


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>spa</title>")
    (tmp_path / "app.js").write_text("console.log('ok')")
    return tmp_path


@pytest.fixture
def settings(static_dir: Path) -> Settings:
    return Settings(app_env=AppEnv.TEST, static_dir=static_dir)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # raise_server_exceptions=False so the real 500 path is exercised, as in production.
    return TestClient(app, raise_server_exceptions=False)


TABLE = "plan-test-sessions"


@pytest.fixture
def dynamodb(monkeypatch):
    """(client, table name) for an in-process DynamoDB (moto) with the table the app expects.

    Built from `app.dynamo.table_definition`, the same layout `infra/` creates, so what the tests
    exercise is what is deployed. No AWS account, network or credentials are involved.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-2")
    with mock_aws():
        client = dynamo.build_client("ap-southeast-2")
        client.create_table(**dynamo.table_definition(TABLE))
        yield client, TABLE


@pytest.fixture(params=["memory", "dynamodb"])
def store_kind(request, monkeypatch):
    """Run a test file's whole-app checks on the in-memory stores, then again on DynamoDB (moto).

    `wire()` builds the conversation from these classes, so swapping them swaps the storage under
    the real routes, service and gate: nothing else in the test changes.
    """
    if request.param == "memory":
        yield "memory"
        return
    from app.conversation import service as service_module
    from tests.interview import test_interview_api as api

    client, table = request.getfixturevalue("dynamodb")
    monkeypatch.setattr(
        api, "InMemoryConversationStore", lambda: DynamoConversationStore(client, table)
    )
    monkeypatch.setattr(api, "InMemoryInterviewStore", lambda: DynamoInterviewStore(client, table))
    monkeypatch.setattr(
        service_module, "InMemoryDeliveryStore", lambda **_: DynamoDeliveryStore(client, table)
    )
    yield "dynamodb"
