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
from app.delivery import DocumentKind, InMemoryDeliveryStore, PurgeGate, evaluate_gate
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


class PurgeRefused(Exception):
    """A deletion that was not accepted. `code` is 'purge_locked' or 'revision_conflict'; `reason`
    is the gate's machine-readable lock reason (or None)."""

    def __init__(self, code: str, reason: str | None) -> None:
        super().__init__(code)
        self.code, self.reason = code, reason


def _has_answer(turns) -> bool:
    """At least one answer from the user is in, so there is something to save."""
    return any(t.role == "user" and t.status == "complete" and t.text.strip() for t in turns)


class ConversationService:
    def __init__(
        self,
        store: Any,
        producer: TurnProducer,
        *,
        clock: Callable[[], datetime] = _utcnow,
        interview: Any = None,
        summarizer: Any = None,
        deliveries: Any = None,
    ) -> None:
        self._store, self._producer, self._clock = store, producer, clock
        self._deliveries = (
            deliveries if deliveries is not None else InMemoryDeliveryStore(clock=clock)
        )
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
            "confirmed_revision": (
                self._interview.confirmed_revision(session.session_id)
                if self._interview is not None
                else None
            ),
        }

    def submit(
        self, session: Session, submission_id: str, text: str, expected_revision: int
    ) -> SubmitResult:
        # Once the user has confirmed the canvas as read back, it is final: no further answers.
        if self._interview is not None:
            confirmed = self._interview.confirmed_revision(session.session_id)
            if confirmed is not None and confirmed == self._store.history(session.session_id)[0]:
                return SubmitResult("confirmed", confirmed)
        return self._store.submit(
            session.session_id,
            submission_id,
            text,
            expected_revision,
            session.expires_at,
            self._clock(),
        )

    def export_snapshot(self, session: Session, allow_incomplete: bool = False) -> ExportSnapshot:
        """One consistent copy of the conversation and draft.

        Normally only while the canvas is complete. With `allow_incomplete` an unfinished draft is
        copied too (marked `complete=False`) so the user can save it and carry on in a new session,
        provided at least one answer is in. Either way the assistant must have finished replying (an
        answer still being processed means the draft may be about to change). If an answer is
        submitted while the copy is being taken, it is taken again, so the revision always matches
        the content.
        """
        if self._interview is None:
            raise AppendixError("not_ready")
        sid = session.session_id
        for _ in range(2):
            revision, turns = self._store.history(sid)
            canvas, declined, ready, confirmed = self._interview.snapshot(sid)
            if self._store.history(sid)[0] == revision:
                if turns and turns[-1].status != "complete":
                    raise AppendixError("unfinished")
                if not ready:
                    if not allow_incomplete:
                        raise AppendixError("not_ready")
                    if not _has_answer(turns):
                        raise AppendixError("nothing_yet")
                return ExportSnapshot(
                    revision, canvas, declined, tuple(turns), confirmed, complete=ready
                )
        raise AppendixError("changed")

    def confirm(self, session: Session, revision: int) -> None:
        """Record that the user confirmed this revision of the canvas after reading it back.

        Only the revision the user was shown, and only while it is still the current, complete one.
        """
        snapshot = self.export_snapshot(session)
        if revision != snapshot.revision:
            raise AppendixError("stale")
        self._interview.confirm(session.session_id, revision, session.expires_at, self._clock())

    def confirmed_snapshot(self, session: Session, revision: int) -> ExportSnapshot:
        """The snapshot for a document, but only if `revision` is the current revision AND the one
        the user confirmed. Everything else is refused, never quietly served."""
        snapshot = self.export_snapshot(session)
        if revision != snapshot.revision:
            raise AppendixError("stale")
        if snapshot.confirmed_revision != snapshot.revision:
            raise AppendixError("not_confirmed")
        return snapshot

    def downloadable_snapshot(self, session: Session, revision: int) -> ExportSnapshot:
        """The snapshot for a document download: the current revision, finished or an unfinished
        draft (with at least one answer in). Confirmation is not needed to save your own work;
        emailing still needs `confirmed_snapshot`. Any other revision is refused, never served."""
        snapshot = self.export_snapshot(session, allow_incomplete=True)
        if revision != snapshot.revision:
            raise AppendixError("stale")
        return snapshot

    def still_confirmed(self, session: Session, revision: int) -> bool:
        """Checked again just before a file is sent: an answer that arrived while it was being
        built must not let an out-of-date canvas through."""
        if self._interview is None:
            return False
        current, _ = self._store.history(session.session_id)
        return (
            current == revision
            and self._interview.confirmed_revision(session.session_id) == revision
        )

    def still_current(self, session: Session, revision: int) -> bool:
        """The download counterpart of `still_confirmed`: no answer has arrived since the file was
        built, so what is being sent is still the current revision."""
        if self._interview is None:
            return False
        current, _ = self._store.history(session.session_id)
        return current == revision

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

    def record_delivery(self, session: Session, kind: DocumentKind, revision: int) -> bool:
        """Note that a document was fully sent, in any format. Only for the revision that is current
        right now, so a response that finishes after an amendment records nothing."""
        if not self.still_current(session, revision):
            return False
        return self._deliveries.record(
            session.session_id, kind, revision, self._clock(), session.expires_at
        )

    def download_gate(self, session: Session) -> PurgeGate:
        """The server's decision on both documents and on deletion, from one consistent read."""
        sid = session.session_id
        revision, turns, ready, confirmed = 0, [], False, None
        stable = False
        for _ in range(2):
            revision, turns = self._store.history(sid)
            if self._interview is not None:
                _, _, ready, confirmed = self._interview.snapshot(sid)
            if self._store.history(sid)[0] == revision:
                stable = True
                break
        return evaluate_gate(
            revision,
            started=self._interview is not None and _has_answer(turns),
            complete=ready and confirmed == revision,
            reply_finished=stable and not any(t.status == "pending" for t in turns[-1:]),
            deliveries=self._deliveries.get(sid),
        )

    def purge(self, session: Session, revision: int) -> None:
        """Delete everything for the session at the user's request, if the gate allows it *now*.

        The gate is evaluated here, never taken from the client, and the final delete only happens
        if no answer has arrived since: a refused purge changes nothing.
        """
        gate = self.download_gate(session)
        if gate.revision != revision:
            raise PurgeRefused("revision_conflict", gate.lock_reason)
        if not gate.purge_eligible:
            raise PurgeRefused("purge_locked", gate.lock_reason)
        if not self._store.delete_session_if_revision(session.session_id, revision):
            raise PurgeRefused("revision_conflict", None)
        self._drop_derived(session.session_id)

    def expired_session_ids(
        self, now: datetime, limit: int, after: str | None = None
    ) -> tuple[list[str], str | None]:
        return self._store.expired_session_ids(now, limit, after)

    def _drop_derived(self, session_id: str) -> None:
        with self._overview_lock:
            self._overviews = {k: v for k, v in self._overviews.items() if k[0] != session_id}
        if self._interview is not None:
            self._interview.delete_session(session_id)
        self._deliveries.delete_session(session_id)

    def delete_session(self, session_id: str) -> None:
        """Drop everything recorded for a session (sign-out, and scheduled expiry). Idempotent."""
        self._store.delete_session(session_id)
        self._drop_derived(session_id)

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
