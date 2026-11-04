"""Delivery records: for each session, the latest revision of each document that was fully sent.

Shaped for DynamoDB (see docs/downloads.md): one small item per (session, document kind), written
with one conditional update ("only if this revision is newer"), read with a strongly consistent
query on the session, and expiring with the session. Nothing here reads another store.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.access.errors import DependencyUnavailable

DocumentKind = Literal["canvas", "appendix"]
DOCUMENT_KINDS: tuple[DocumentKind, ...] = ("canvas", "appendix")

MAX_TRACKED_SESSIONS = 10_000


@dataclass(frozen=True)
class Delivery:
    kind: DocumentKind
    revision: int  # the reviewed revision the file was built from
    completed_at: datetime


class DeliveryStore(Protocol):
    def record(
        self,
        session_id: str,
        kind: DocumentKind,
        revision: int,
        completed_at: datetime,
        expires_at: datetime,
    ) -> bool:
        """Note that `kind` was fully sent for `revision`. Idempotent, and never goes backwards:
        a record for an older revision than the one held is ignored. True if it was kept (or was
        already there), False if it was ignored or there was no room."""
        ...

    def get(self, session_id: str) -> dict[DocumentKind, Delivery]:
        """The records for this session only, as they are now."""
        ...

    def delete_session(self, session_id: str) -> None: ...


class InMemoryDeliveryStore:
    """Local and test only, like the other in-memory stores: lost on restart and not shared."""

    def __init__(self, capacity: int = MAX_TRACKED_SESSIONS) -> None:
        self._lock = threading.Lock()
        self._capacity = capacity
        # session -> (records, session end)
        self._sessions: dict[str, tuple[dict[DocumentKind, Delivery], datetime]] = {}

    def record(
        self,
        session_id: str,
        kind: DocumentKind,
        revision: int,
        completed_at: datetime,
        expires_at: datetime,
    ) -> bool:
        with self._lock:
            # Entries end with their session; a live one is never dropped to make room.
            self._sessions = {k: v for k, v in self._sessions.items() if v[1] > completed_at}
            if session_id not in self._sessions:
                if len(self._sessions) >= self._capacity:
                    return False
                self._sessions[session_id] = ({}, expires_at)
            held = self._sessions[session_id][0].get(kind)
            if held is not None and held.revision >= revision:
                return held.revision == revision  # same revision again is fine; older is ignored
            self._sessions[session_id][0][kind] = Delivery(kind, revision, completed_at)
            return True

    def get(self, session_id: str) -> dict[DocumentKind, Delivery]:
        with self._lock:
            entry = self._sessions.get(session_id)
            return dict(entry[0]) if entry else {}

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class UnavailableDeliveryStore:
    """Used when deployed until a durable store exists: records fail closed, like the others."""

    def _fail(self, *_, **__):
        raise DependencyUnavailable("delivery_store_unavailable")

    record = get = _fail

    def delete_session(self, session_id: str) -> None:
        return None
