"""Server-side session records, and single-use grant claims.

Sessions are keyed by an unpredictable server-generated id. `expires_at` is fixed at
creation (24 h) and is never extended. DynamoDB TTL removes expired items lazily (up to
~48 h later), so the application checks `expires_at` itself on every read.

Table: partition key `session_id` (String); TTL attribute `expires_at` (epoch seconds).
Grants use the same table under the key prefix `grant#`, which cannot collide with a
session id (those are URL-safe base64 and contain no '#').
"""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from app.access.errors import DependencyUnavailable

_GRANT_PREFIX = "grant#"


@dataclass(frozen=True)
class Session:
    session_id: str
    created_at: datetime  # UTC
    expires_at: datetime  # UTC, fixed at creation


class SessionStore(Protocol):
    def create(self, session: Session) -> None: ...

    def get(self, session_id: str) -> Session | None: ...

    def delete(self, session_id: str) -> None: ...

    def claim_grant(self, grant_id: str, expires_at: datetime, now: datetime) -> bool:
        """Atomically mark a grant used. False if it was already claimed.

        `now` is the caller's clock. An expired grant is already refused at signature time,
        so a store may forget claims whose `expires_at` has passed without allowing a replay.
        """
        ...


class InMemorySessionStore:
    """Local development and tests only: state is lost on restart and not shared.

    Expired grants and sessions are dropped as new ones arrive, so a long-running local
    process does not grow without bound. (DynamoDB relies on its TTL attribute instead.)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        self._grants: dict[str, datetime] = {}  # grant id -> its expiry

    def create(self, session: Session) -> None:
        with self._lock:
            # session.created_at is the service clock's "now"; anything expired by then is gone.
            self.sessions = {
                sid: s for sid, s in self.sessions.items() if s.expires_at > session.created_at
            }
            if session.session_id in self.sessions:
                raise DependencyUnavailable("session_id_collision")
            self.sessions[session.session_id] = session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self.sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        with self._lock:
            self.sessions.pop(session_id, None)

    def claim_grant(self, grant_id: str, expires_at: datetime, now: datetime) -> bool:
        with self._lock:
            # Safe to forget expired claims: their cookies are rejected before reaching here.
            self._grants = {g: exp for g, exp in self._grants.items() if exp > now}
            if grant_id in self._grants:
                return False
            self._grants[grant_id] = expires_at
            return True


class DynamoSessionStore:
    def __init__(self, client: Any, table_name: str) -> None:
        self._client, self._table = client, table_name

    def _put_new(self, item: dict[str, Any]) -> bool:
        """Conditional put: never overwrites an existing record. False if the key existed."""
        try:
            self._client.put_item(
                TableName=self._table,
                Item=item,
                ConditionExpression="attribute_not_exists(session_id)",
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise DependencyUnavailable("store") from None
        except BotoCoreError:
            raise DependencyUnavailable("store") from None

    def create(self, session: Session) -> None:
        item = {
            "session_id": {"S": session.session_id},
            "created_at": {"S": session.created_at.isoformat()},
            "expires_at": {"N": str(int(session.expires_at.timestamp()))},
        }
        if not self._put_new(item):
            raise DependencyUnavailable("session_id_collision")

    def get(self, session_id: str) -> Session | None:
        try:
            response = self._client.get_item(
                TableName=self._table,
                Key={"session_id": {"S": session_id}},
                ConsistentRead=True,
            )
        except (ClientError, BotoCoreError):
            raise DependencyUnavailable("store") from None
        item = response.get("Item")
        if not item:
            return None
        try:
            return Session(
                session_id=item["session_id"]["S"],
                created_at=datetime.fromisoformat(item["created_at"]["S"]).replace(tzinfo=UTC),
                expires_at=datetime.fromtimestamp(int(item["expires_at"]["N"]), UTC),
            )
        except (KeyError, ValueError):
            raise DependencyUnavailable("store_record_invalid") from None

    def delete(self, session_id: str) -> None:
        try:
            self._client.delete_item(TableName=self._table, Key={"session_id": {"S": session_id}})
        except (ClientError, BotoCoreError):
            raise DependencyUnavailable("store") from None

    def claim_grant(self, grant_id: str, expires_at: datetime, now: datetime) -> bool:
        return self._put_new(
            {
                "session_id": {"S": _GRANT_PREFIX + grant_id},
                "expires_at": {"N": str(int(expires_at.timestamp()))},
            }
        )
