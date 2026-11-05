"""How often a session may send, and one send at a time.

Kept in memory like the rest of the session state. Entries end when the session does, so a full
table is never cleared early: a new session is refused instead (fail closed). Behind several
tasks the count is per task; the SES account quota is the shared backstop (see docs/email.md).
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime

MAX_SENDS_PER_SESSION = 5
MAX_TRACKED_SESSIONS = 10_000


class SendRefused(Exception):
    """`code` is 'in_progress' (a send is already running), 'limit' or 'full'."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SendLimiter:
    def __init__(
        self,
        per_session: int = MAX_SENDS_PER_SESSION,
        capacity: int = MAX_TRACKED_SESSIONS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._per_session, self._capacity, self._clock = per_session, capacity, clock
        self._sent: dict[str, tuple[int, datetime]] = {}  # session -> (emails sent, session end)
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def begin(self, session_id: str, expires_at: datetime) -> None:
        with self._lock:
            now = self._clock()
            for key in [k for k, (_, end) in self._sent.items() if end <= now]:
                del self._sent[key]
            if session_id in self._running:
                raise SendRefused("in_progress")
            count = self._sent.get(session_id, (0, expires_at))[0]
            if count >= self._per_session:
                raise SendRefused("limit")
            if session_id not in self._sent and len(self._sent) >= self._capacity:
                raise SendRefused("full")
            self._running.add(session_id)

    def finish(self, session_id: str, expires_at: datetime, *, sent: bool) -> None:
        """Ends a send. Only an email handed to the mail service counts against the limit."""
        with self._lock:
            self._running.discard(session_id)
            if sent:
                count = self._sent.get(session_id, (0, expires_at))[0]
                self._sent[session_id] = (count + 1, expires_at)

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._sent.pop(session_id, None)
            self._running.discard(session_id)
