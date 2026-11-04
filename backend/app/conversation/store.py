"""Authoritative, ordered turns per session, plus idempotent submission and revision control.

This is the minimal contract the conversation screen needs. Everything is scoped by session id,
so one session can never read or change another's turns. The in-memory store is for local use
and tests; deployed runs fail closed until a durable store exists (see wiring).
"""

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

from app.access.errors import DependencyUnavailable

MAX_TURNS = 100  # per session; bounds memory and runaway loops

Role = Literal["user", "assistant"]
Status = Literal["complete", "pending", "failed"]


@dataclass(frozen=True)
class Turn:
    turn_id: str  # server-generated, unguessable
    role: Role
    text: str
    status: Status


@dataclass(frozen=True)
class SubmitResult:
    outcome: Literal["created", "replayed", "revision_conflict", "unfinished", "full"]
    revision: int
    user_turn: Turn | None = None
    assistant_turn: Turn | None = None


@dataclass
class _Conversation:
    expires_at: datetime
    revision: int = 0
    turns: list[Turn] = field(default_factory=list)
    submissions: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> (user, assistant)


def _new_id() -> str:
    return uuid.uuid4().hex


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conversations: dict[str, _Conversation] = {}

    def _prune(self, now: datetime) -> None:
        self._conversations = {k: c for k, c in self._conversations.items() if c.expires_at > now}

    def history(self, session_id: str) -> tuple[int, list[Turn]]:
        with self._lock:
            conv = self._conversations.get(session_id)
            return (conv.revision, list(conv.turns)) if conv else (0, [])

    def submit(
        self,
        session_id: str,
        submission_id: str,
        text: str,
        expected_revision: int,
        session_expires_at: datetime,
        now: datetime,
    ) -> SubmitResult:
        """Record a user answer and a pending assistant turn, atomically and at most once."""
        with self._lock:
            self._prune(now)
            conv = self._conversations.setdefault(session_id, _Conversation(session_expires_at))
            by_id = {t.turn_id: t for t in conv.turns}

            if submission_id in conv.submissions:  # a retry of an already-recorded answer
                user_id, assistant_id = conv.submissions[submission_id]
                return SubmitResult("replayed", conv.revision, by_id[user_id], by_id[assistant_id])
            if expected_revision != conv.revision:
                return SubmitResult("revision_conflict", conv.revision)
            if conv.turns and conv.turns[-1].status != "complete":
                return SubmitResult("unfinished", conv.revision)
            if len(conv.turns) + 2 > MAX_TURNS:
                return SubmitResult("full", conv.revision)

            user = Turn(_new_id(), "user", text, "complete")
            assistant = Turn(_new_id(), "assistant", "", "pending")
            conv.turns += [user, assistant]
            conv.revision += 1
            conv.submissions[submission_id] = (user.turn_id, assistant.turn_id)
            return SubmitResult("created", conv.revision, user, assistant)

    def _find(self, session_id: str, turn_id: str) -> Turn | None:
        conv = self._conversations.get(session_id)
        return next((t for t in conv.turns if t.turn_id == turn_id), None) if conv else None

    def get_turn(self, session_id: str, turn_id: str) -> Turn | None:
        with self._lock:
            return self._find(session_id, turn_id)

    def context_for(self, session_id: str, turn_id: str) -> tuple[str, str, tuple[str, ...]]:
        """(answered user turn id, its text, all user turn ids) for an assistant turn."""
        with self._lock:
            conv = self._conversations.get(session_id)
            turns = conv.turns if conv else []
            user_ids = tuple(t.turn_id for t in turns if t.role == "user")
            for i, t in enumerate(turns):
                if t.turn_id == turn_id and i > 0:
                    return turns[i - 1].turn_id, turns[i - 1].text, user_ids
            return "", "", user_ids

    def _replace(self, session_id: str, turn: Turn, **changes) -> Turn:
        conv = self._conversations[session_id]
        index = conv.turns.index(turn)
        conv.turns[index] = replace(turn, **changes)
        return conv.turns[index]

    def complete_turn(self, session_id: str, turn_id: str, text: str) -> tuple[Turn | None, bool]:
        """Commit the assistant's reply. Set-once; the flag says whether this call did it."""
        with self._lock:
            turn = self._find(session_id, turn_id)
            if turn is None or turn.status == "complete":
                return turn, False
            return self._replace(session_id, turn, text=text, status="complete"), True

    def mark(self, session_id: str, turn_id: str, status: Status) -> None:
        """Move a not-yet-complete assistant turn between pending and failed."""
        with self._lock:
            turn = self._find(session_id, turn_id)
            if turn is not None and turn.status != "complete":
                self._replace(session_id, turn, text="", status=status)

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conversations.pop(session_id, None)


class UnavailableConversationStore:
    """Used when deployed until a durable store exists: every call fails closed with a 503.

    Silently using per-process memory behind a load balancer would lose or split transcripts.
    """

    def _fail(self, *_, **__):
        raise DependencyUnavailable("conversation_store_unavailable")

    history = submit = get_turn = context_for = complete_turn = mark = _fail

    def delete_session(self, session_id: str) -> None:  # nothing is stored, nothing to delete
        return None
