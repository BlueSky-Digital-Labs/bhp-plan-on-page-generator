import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from app.access.store import Session
from app.appendix.model import AppendixError, ExportSnapshot
from app.conversation.producers import ProducerUnavailable, TurnContext, TurnProducer
from app.conversation.store import SubmitResult, Turn
from app.interview.canvas import REQUIRED_ORDER

logger = logging.getLogger("app.conversation")

_MAX_OVERVIEWS = 200
MAX_REPLY_CHARS = 8000  # a runaway producer cannot grow a stored turn without bound


def _safe_code(code: object) -> str | None:
    return code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,40}", code) else None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _empty_canvas_view() -> dict[str, Any]:
    """The draft when no interviewer is attached: nothing answered, every required field to go."""
    return {"fields": {}, "missing_required": list(REQUIRED_ORDER), "ready_for_review": False}


def _empty_progression_view() -> dict[str, Any]:
    return {
        "phase": "interview",
        "outstanding_question": None,
        "clarification": {"active": False, "reason": None},
        "validation": {"missing_required": list(REQUIRED_ORDER), "unsure": []},
        "data_and_systems_complete": False,
        "priority_follow_up_asked": False,
        "optional_prompted": [],
        "optional_declined": [],
    }


def turn_json(turn: Turn) -> dict[str, str]:
    return {"turn_id": turn.turn_id, "role": turn.role, "text": turn.text, "status": turn.status}


class ConversationService:
    def __init__(
        self,
        store: Any,
        producer: TurnProducer,
        *,
        clock: Callable[[], datetime] = _utcnow,
        interview: Any = None,
        summarizer: Any = None,
    ) -> None:
        self._store, self._producer, self._clock = store, producer, clock
        self._interview = interview  # optional: where the canvas draft lives
        self._summarizer = summarizer  # optional: writes the appendix overview
        self._overviews: dict[tuple[str, int], str] = {}  # (session, revision) -> overview
        self._overview_lock = threading.Lock()

    def history(self, session: Session) -> dict[str, Any]:
        revision, turns = self._store.history(session.session_id)
        canvas = (
            self._interview.canvas_view(session.session_id)
            if self._interview is not None
            else _empty_canvas_view()
        )
        progression = (
            self._interview.progression_view(session.session_id)
            if self._interview is not None
            else _empty_progression_view()
        )
        return {
            "revision": revision,
            "turns": [turn_json(t) for t in turns],
            "canvas": canvas,
            "progression": progression,
        }

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

    def export_snapshot(self, session: Session) -> ExportSnapshot:
        """One consistent copy of the confirmed conversation and draft, for the appendix.

        Only a canvas the server says is ready can be exported. If an answer is submitted while
        the copy is being taken, it is taken again, so the revision always matches the content.
        """
        if self._interview is None:
            raise AppendixError("not_ready")
        sid = session.session_id
        for _ in range(2):
            revision, turns = self._store.history(sid)
            canvas, declined, ready = self._interview.snapshot(sid)
            if self._store.history(sid)[0] == revision:
                if not ready:
                    raise AppendixError("not_ready")
                return ExportSnapshot(revision, canvas, declined, tuple(turns))
        raise AppendixError("changed")

    def overview_for(self, session: Session, revision: int, turns: tuple[Any, ...]) -> str | None:
        """The AI-written overview of this revision of the conversation, or None.

        Written once per revision and kept only in memory (so the three file formats agree and a
        second download is quick). Any failure means no overview, never a failed download.
        """
        if self._summarizer is None or not turns:
            return None
        key = (session.session_id, revision)
        with self._overview_lock:
            if key in self._overviews:
                return self._overviews[key]
        try:
            overview = self._summarizer.summarize(turns)
        except Exception as exc:  # noqa: BLE001 - the appendix does not depend on it
            logger.warning("summary_failed", extra={"reason": type(exc).__name__[:40]})
            return None
        if overview:
            with self._overview_lock:
                if len(self._overviews) >= _MAX_OVERVIEWS:
                    self._overviews.pop(next(iter(self._overviews)))  # the oldest
                self._overviews[key] = overview
        return overview

    def delete_session(self, session_id: str) -> None:
        """Drop everything recorded for a session (used when the session is ended)."""
        with self._overview_lock:
            self._overviews = {k: v for k, v in self._overviews.items() if k[0] != session_id}
        self._store.delete_session(session_id)
        if self._interview is not None:
            self._interview.delete_session(session_id)

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
        user_turn_id, user_text, user_turn_ids = self._store.context_for(sid, turn.turn_id)
        ctx = TurnContext(
            session_id=sid,
            turn_id=turn.turn_id,
            user_turn_id=user_turn_id,
            user_text=user_text,
            user_turn_ids=user_turn_ids,
            session_expires_at=session.expires_at,
        )
        parts: list[str] = []
        size = 0
        try:
            for chunk in self._producer.stream(ctx):
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
                extra={
                    "reason": "processing_error",
                    "exception_type": type(exc).__name__,
                    # Only our own content-free codes (e.g. ExtractionFailed), never exception text.
                    "failure_code": _safe_code(getattr(exc, "code", None)),
                    "detail": "; ".join(getattr(exc, "detail", ()))[:200] or None,
                },
            )
            yield line({"type": "failed", "reason": "processing_error"})
            return

        _, first = self._store.complete_turn(sid, turn.turn_id, "".join(parts))
        if first:  # side effects (canvas draft, prompt markers) happen once, with the turn
            commit = getattr(self._producer, "commit", None)
            if commit is not None:
                commit(ctx)
        yield line({"type": "complete"})
