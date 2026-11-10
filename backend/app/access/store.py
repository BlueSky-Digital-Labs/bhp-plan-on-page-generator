"""Server-side session records, and single-use grant claims.

Sessions are keyed by an unpredictable server-generated id. `expires_at` is fixed at
creation (24 h) and is never extended. DynamoDB TTL removes expired items lazily (up to
~48 h later), so the application checks `expires_at` itself on every read.

Table: partition key `session_id` (String), sort key `sk` (String); TTL attribute `expires_at`
(epoch seconds); the table is shared with the conversation content (see app/dynamo.py). A sign-in
record is `sk = "ACCESS"`. Grants use the key prefix `grant#` (sk "GRANT"), which cannot collide
with a session id (those are URL-safe base64 and contain no '#').
"""

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError

from app.access.errors import DependencyUnavailable

# A session with no activity for this long ends and its data is deleted. Separately, it ends 24
# hours after it began whatever happens (`expires_at`).
SESSION_IDLE = timedelta(minutes=15)

_GRANT_PREFIX = "grant#"
_ACCESS, _GRANT = "ACCESS", "GRANT"  # the sort key of each kind of record


@dataclass(frozen=True)
class Session:
    session_id: str
    created_at: datetime  # UTC
    expires_at: datetime  # UTC, fixed at creation
    last_active_at: datetime | None = None  # UTC; None means "at creation"

    @property
    def idle_since(self) -> datetime:
        return self.last_active_at or self.created_at

    def idle_deadline(self) -> datetime:
        """When the session ends for want of activity (never later than its fixed 24 h end)."""
        return min(self.expires_at, self.idle_since + SESSION_IDLE)


class SessionStore(Protocol):
    def create(self, session: Session) -> None: ...

    def get(self, session_id: str) -> Session | None: ...

    def delete(self, session_id: str) -> None: ...

    def touch(self, session_id: str, now: datetime) -> None:
        """Record activity. Never revives a session that is already gone."""
        ...

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

    def touch(self, session_id: str, now: datetime) -> None:
        with self._lock:
            current = self.sessions.get(session_id)
            if current is not None:
                self.sessions[session_id] = replace(current, last_active_at=now)

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
            "sk": {"S": _ACCESS},
            "created_at": {"S": session.created_at.isoformat()},
            "expires_at": {"N": str(int(session.expires_at.timestamp()))},
        }
        if not self._put_new(item):
            raise DependencyUnavailable("session_id_collision")

    def get(self, session_id: str) -> Session | None:
        try:
            response = self._client.get_item(
                TableName=self._table,
                Key={"session_id": {"S": session_id}, "sk": {"S": _ACCESS}},
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
                last_active_at=(
                    datetime.fromtimestamp(int(item["last_active_at"]["N"]), UTC)
                    if "last_active_at" in item
                    else None
                ),
            )
        except (KeyError, ValueError):
            raise DependencyUnavailable("store_record_invalid") from None

    def delete(self, session_id: str) -> None:
        try:
            self._client.delete_item(
                TableName=self._table, Key={"session_id": {"S": session_id}, "sk": {"S": _ACCESS}}
            )
        except (ClientError, BotoCoreError):
            raise DependencyUnavailable("store") from None

    def touch(self, session_id: str, now: datetime) -> None:
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"session_id": {"S": session_id}, "sk": {"S": _ACCESS}},
                UpdateExpression="SET last_active_at = :now",
                ConditionExpression="attribute_exists(session_id)",
                ExpressionAttributeValues={":now": {"N": str(int(now.timestamp()))}},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return  # it ended meanwhile: do not bring it back
            raise DependencyUnavailable("store") from None
        except BotoCoreError:
            raise DependencyUnavailable("store") from None

    def claim_grant(self, grant_id: str, expires_at: datetime, now: datetime) -> bool:
        return self._put_new(
            {
                "session_id": {"S": _GRANT_PREFIX + grant_id},
                "sk": {"S": _GRANT},
                "expires_at": {"N": str(int(expires_at.timestamp()))},
            }
        )
