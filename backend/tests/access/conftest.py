"""Fixtures for access-control tests. All secrets and codes here are synthetic."""

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.access.errors import DependencyUnavailable
from app.access.service import AccessService
from app.access.store import InMemorySessionStore
from app.config import AppEnv, Settings
from app.main import create_app

CODE = "synthetic-shared-code-123"
NEW_CODE = "synthetic-rotated-code-456"
KEY = b"k" * 48  # cookie-signing master key
# Cheap parameters so the suite stays fast; the verifier reads parameters from the hash.
_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

GRANT_COOKIE = "__Host-grant"
SESSION_COOKIE = "__Host-session"
ORIGIN = "https://testserver"


def hash_code(code: str) -> str:
    return _HASHER.hash(code)


class FakeSecrets:
    def __init__(self) -> None:
        self.hash = hash_code(CODE)
        self.key = KEY
        self.fail = False
        self.hash_lookups = 0

    def access_code_hash(self) -> str:
        if self.fail:
            raise DependencyUnavailable("secret_lookup")
        self.hash_lookups += 1
        return self.hash

    def cookie_signing_key(self) -> bytes:
        if self.fail:
            raise DependencyUnavailable("secret_lookup")
        return self.key


class FlakyStore(InMemorySessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise DependencyUnavailable("store")

    def create(self, session):
        self._check()
        super().create(session)

    def get(self, session_id):
        self._check()
        return super().get(session_id)

    def claim_grant(self, grant_id, expires_at, now):
        self._check()
        return super().claim_grant(grant_id, expires_at, now)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class World:
    secrets: FakeSecrets
    store: FlakyStore
    clock: Clock
    service: AccessService

    def client(self, **headers) -> TestClient:
        """An independent 'browser': its own cookie jar, https origin, same-origin header."""
        client = TestClient(
            create_app(self._settings, access_service=self.service),
            base_url=ORIGIN,
            raise_server_exceptions=False,
        )
        client.headers.update({"Origin": ORIGIN, **headers})
        return client

    _settings: Settings = None  # set by the fixture


@pytest.fixture
def world(tmp_path) -> World:
    secrets, store, clock = FakeSecrets(), FlakyStore(), Clock()
    w = World(secrets, store, clock, AccessService(secrets, store, clock=clock))
    (tmp_path / "index.html").write_text("<!doctype html>")
    w._settings = Settings(app_env=AppEnv.TEST, static_dir=tmp_path)
    return w


# --- helpers -------------------------------------------------------------------------


def csrf(client: TestClient) -> dict[str, str]:
    token = client.get("/api/csrf").json()["csrf_token"]
    return {"X-CSRF-Token": token}


def enter(client: TestClient, code: str = CODE):
    return client.post("/api/access", data={"access_code": code}, headers=csrf(client))


def start(client: TestClient):
    return client.post("/api/sessions", headers=csrf(client))


def signed_in(world: World) -> TestClient:
    """A browser that has entered the code and started its own session."""
    client = world.client()
    assert enter(client).status_code == 200
    assert start(client).status_code == 201
    return client


def set_cookies(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def cookie_payload(value: str) -> dict:
    body = value.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
