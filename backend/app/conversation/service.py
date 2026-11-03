import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from app.access.store import Session
from app.conversation.producers import ProducerUnavailable, TurnProducer
from app.conversation.store import SubmitResult, Turn

logger = logging.getLogger("app.conversation")

MAX_REPLY_CHARS = 8000  # a runaway producer cannot grow a stored turn without bound


def _utcnow() -> datetime:
    return datetime.now(UTC)


def turn_json(turn: Turn) -> dict[str, str]:
    return {"turn_id": turn.turn_id, "role": turn.role, "text": turn.text, "status": turn.status}


class ConversationService:
    def __init__(
        self,
        store: Any,
        producer: TurnProducer,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store, self._producer, self._clock = store, producer, clock

    def history(self, session: Session) -> dict[str, Any]:
        revision, turns = self._store.history(session.session_id)
        return {"revision": revision, "turns": [turn_json(t) for t in turns]}

    def submit(
        self, session: Session, submission_id: str, text: str, expected_revision: int
    ) -> SubmitResult:
        return self._store.submit(
            session.session_id,
            submission_id,
            text,
            expected_revision,
            session.expires_at,
            self._clock(),
        )

    def delete_session(self, session_id: str) -> None:
        """Drop everything recorded for a session (used when the session is ended)."""
        self._store.delete_session(session_id)

    def get_assistant_turn(self, session: Session, turn_id: str) -> Turn | None:
        """Only the caller's own assistant turns: anything else looks like 'not found'."""
        turn = self._store.get_turn(session.session_id, turn_id)
        return turn if turn is not None and turn.role == "assistant" else None

    def stream(self, session: Session, turn: Turn) -> Iterator[bytes]:
        """Structured progress as NDJSON: delta* then exactly one complete|failed.

        Fragments are transient. The reply is committed to the store only when the producer
        finishes, so a dropped connection leaves the turn pending for a clean retry.
        """

        def line(event: dict[str, Any]) -> bytes:
            return (json.dumps(event) + "\n").encode()

        if turn.status == "complete":  # authoritative text wins; no regeneration
            yield line({"type": "delta", "text": turn.text})
            yield line({"type": "complete"})
            return

        sid = session.session_id
        self._store.mark(sid, turn.turn_id, "pending")  # a failed turn is retried from scratch
        parts: list[str] = []
        size = 0
        try:
            for chunk in self._producer.stream(self._store.preceding_user_text(sid, turn.turn_id)):
                size += len(chunk)
                if size > MAX_REPLY_CHARS:
                    raise ValueError("reply too long")
                parts.append(chunk)
                yield line({"type": "delta", "text": chunk})
        except ProducerUnavailable:
            self._store.mark(sid, turn.turn_id, "failed")
            logger.info("turn_failed", extra={"reason": "unavailable"})
            yield line({"type": "failed", "reason": "unavailable"})
            return
        except Exception as exc:  # noqa: BLE001 - never leak producer/model detail to the client
            self._store.mark(sid, turn.turn_id, "failed")
            logger.error(
                "turn_failed",
                extra={"reason": "processing_error", "exception_type": type(exc).__name__},
            )
            yield line({"type": "failed", "reason": "processing_error"})
            return

        self._store.complete_turn(sid, turn.turn_id, "".join(parts))
        yield line({"type": "complete"})
