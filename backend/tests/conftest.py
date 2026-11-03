import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppEnv, Settings, get_secrets, get_settings
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
