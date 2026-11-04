import copy
import threading
from datetime import datetime

from app.interview.engine import InterviewState, canvas_view, is_ready, progression_view


class InMemoryInterviewStore:
    """The interview state, per session. Local and test only, like the conversation store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, tuple[InterviewState, datetime]] = {}

    def get(self, session_id: str) -> InterviewState:
        with self._lock:
            entry = self._states.get(session_id)
            return copy.deepcopy(entry[0]) if entry else InterviewState()

    def put(
        self, session_id: str, state: InterviewState, expires_at: datetime, now: datetime
    ) -> None:
        with self._lock:
            # Sessions keep their original expiry; state for expired ones is dropped.
            self._states = {k: v for k, v in self._states.items() if v[1] > now}
            self._states[session_id] = (copy.deepcopy(state), expires_at)

    def canvas_view(self, session_id: str) -> dict:
        return canvas_view(self.get(session_id))

    def progression_view(self, session_id: str) -> dict:
        return progression_view(self.get(session_id))

    def snapshot(self, session_id: str) -> tuple[dict, tuple[str, ...], bool, int | None]:
        """The draft, the optional questions skipped, whether it is ready, and the revision the
        user confirmed (or None): one copy."""
        state = self.get(session_id)
        return (
            dict(state.canvas),
            tuple(sorted(state.declined)),
            is_ready(state),
            state.confirmed_revision,
        )

    def confirmed_revision(self, session_id: str) -> int | None:
        return self.get(session_id).confirmed_revision

    def confirm(self, session_id: str, revision: int, expires_at: datetime, now: datetime) -> None:
        state = self.get(session_id)
        state.confirmed_revision = revision
        self.put(session_id, state, expires_at, now)

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._states.pop(session_id, None)
