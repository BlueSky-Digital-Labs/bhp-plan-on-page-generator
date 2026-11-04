"""The interview, as deterministic code around an untrusted extractor.

An extractor (a model or the local test stand-in) may only *propose* field updates. This module
validates every proposal, applies it to a copy of the state, and picks the next question itself
from the validator's gaps. Nothing here talks to a model or writes anywhere: `process` returns the
new state, and the caller commits it together with the assistant turn, so an interrupted turn
changes nothing.
"""

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.interview.canvas import (
    ALL_FIELDS,
    OPPORTUNITY_TYPES,
    OPTIONAL_FIELDS,
    PRIORITIES,
    Canvas,
    clean_value,
    missing_required,
)
from app.interview.prompts import (
    ACK,
    READY_TEXT,
    render_question,
    validate_question,
)

Kind = Literal["missing", "clarify", "confirm", "optional"]
Stance = Literal["answered", "unsure", "declined", "affirm"]


@dataclass(frozen=True)
class Target:
    """The one information need the next question is about. Owned by the server."""

    kind: Kind
    field: str


@dataclass
class InterviewState:
    canvas: Canvas = field(default_factory=dict)
    target: Target | None = None  # persisted so a retry or refresh asks for the same thing
    prompted: set[str] = field(default_factory=set)  # optional fields whose question was shown
    declined: set[str] = field(default_factory=set)  # optional fields the user declined/skipped
    confirmed: set[str] = field(default_factory=set)  # data / systems confirmation turns done
    unknown: set[str] = field(default_factory=set)  # required fields the user said they don't know
    attempts: dict[str, int] = field(default_factory=dict)
    conflicts: dict[str, Any] = field(default_factory=dict)  # field -> new value awaiting a choice


@dataclass
class FieldUpdate:
    field: str
    value: Any
    source_turn_ids: list[str]


@dataclass
class Extraction:
    field_updates: list[FieldUpdate] = field(default_factory=list)
    next_question: str | None = None  # optional; used only if it passes validate_question
    action: str = "ask"


@dataclass(frozen=True)
class ExtractionInput:
    user_text: str
    user_turn_id: str
    user_turn_ids: tuple[str, ...]
    target: Target | None
    canvas: Canvas
    override_attempt: bool  # the message tries to change the assistant's rules


class Extractor(Protocol):
    def extract(self, inp: ExtractionInput) -> Extraction: ...


class ExtractionFailed(Exception):
    """The extractor could not produce a usable result. The turn fails and can be retried."""

    def __init__(self, code: str, detail: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail  # safe, content-free diagnostics (schema paths and error types)


@dataclass
class Outcome:
    text: str
    state: InterviewState
    action: Literal["ask", "clarify", "ready_for_review"]


# --- reading the user's message (deterministic, never delegated to the model) -------------------
_UNSURE = re.compile(
    r"\b(i\s+(do\s*n[o']?t|dont|don't)\s+know|not sure|unsure|no idea|unknown|can'?t say)\b"
)
_DECLINE = re.compile(
    r"\b(skip|pass|prefer not|rather not|decline|n/a|not applicable|no comment)\b"
)
_AFFIRM = re.compile(
    r"^(yes|yep|yeah|correct|right|that'?s (right|all|it)|that is (right|all|it)|all good|"
    r"nothing else|no,? that'?s all|looks good|confirmed?)\b"
)
_OVERRIDE = re.compile(
    r"\b(ignore (all |any |the |your |previous |the above )*(instructions|rules|prompt)|"
    r"disregard (your|the|all)|forget (your|all|the) (instructions|rules)|you are now|"
    r"act as|pretend (to be|you)|system prompt|reveal your|new instructions)\b"
)
_SHORT = 60  # only brief replies are read as "unsure" or "declined"; long ones carry content


def detect_stance(text: str, target: Target | None) -> Stance:
    t = text.strip().casefold()
    if len(t) <= _SHORT:
        if _UNSURE.search(t):
            return "unsure"
        if _DECLINE.search(t) or t in {"no", "none", "nope", "n/a", "na"}:
            return "declined"
        if target is not None and target.kind == "confirm" and _AFFIRM.match(t):
            return "affirm"
    return "answered"


def looks_like_override(text: str) -> bool:
    return bool(_OVERRIDE.search(text.casefold()))


# --- choosing the next question from the gaps --------------------------------------------------
def choose_target(s: InterviewState) -> Target | None:
    """The single next information need, or None when nothing is left to ask."""
    if s.conflicts:
        return Target("clarify", next(iter(s.conflicts)))
    missing = missing_required(s.canvas)
    if missing:
        # A required field the user could not answer is revisited later, never dropped.
        pick = next((f for f in missing if s.attempts.get(f, 0) < 2), missing[0])
        return Target("clarify" if s.attempts.get(pick, 0) else "missing", pick)
    for f in ("data", "systems"):  # dedicated passes, one per turn, even if already mentioned
        if f not in s.confirmed:
            return Target("confirm", f)
    for f in OPTIONAL_FIELDS:  # prompted separately; declining never blocks
        if f not in s.prompted:
            return Target("optional", f)
    return None


def is_ready(s: InterviewState) -> bool:
    """Ready only when the validator is satisfied, every obligation is met AND no question is
    outstanding (the target is cleared only by the turn that declares readiness)."""
    return (
        not s.conflicts
        and not missing_required(s.canvas)
        and s.target is None
        and choose_target(s) is None
    )


def canvas_view(s: InterviewState) -> dict[str, Any]:
    """What the browser may see of the draft."""
    return {
        "fields": dict(s.canvas),
        "missing_required": missing_required(s.canvas),
        "ready_for_review": is_ready(s),
    }


class Interviewer:
    def __init__(self, extractor: Extractor) -> None:
        self._extractor = extractor

    def process(
        self,
        state: InterviewState,
        user_text: str,
        user_turn_id: str,
        user_turn_ids: tuple[str, ...],
    ) -> Outcome:
        s = copy.deepcopy(state)
        target = s.target
        stance = detect_stance(user_text, target)
        override = looks_like_override(user_text)
        cue: str | None = None

        if target is not None and target.kind == "confirm":
            captured = self._confirm(s, target.field, user_text, stance, override)
            s.confirmed.add(target.field)
            cue = ACK["captured"] if captured else ACK["confirmed"]
        elif target is not None and target.kind == "clarify" and target.field in s.conflicts:
            self._resolve_conflict(s, target.field, user_text)
            cue = ACK["captured"]
        elif target is not None and stance in ("unsure", "declined", "affirm"):
            self._note_non_answer(s, target, stance)
            cue = ACK["declined"] if target.kind == "optional" else ACK["unsure"]
        else:
            extraction = self._extractor.extract(
                ExtractionInput(user_text, user_turn_id, user_turn_ids, target, s.canvas, override)
            )
            captured = self._apply(s, extraction, target, user_text, user_turn_ids)
            if override:
                cue = ACK["override"]
            elif s.conflicts:
                cue = ACK["conflict"]
            elif captured:
                cue = ACK["captured"]
            else:  # nothing usable was captured
                if target is not None and target.kind == "optional":
                    s.declined.add(target.field)  # an optional answer we cannot use never blocks
                elif target is not None:
                    s.attempts[target.field] = s.attempts.get(target.field, 0) + 1
                cue = ACK["nothing"]
            extraction_question = extraction.next_question
            return self._finish(s, cue, extraction_question)

        return self._finish(s, cue, None)

    # -- applying proposals ---------------------------------------------------------------------
    def _apply(
        self,
        s: InterviewState,
        extraction: Extraction,
        target: Target | None,
        user_text: str,
        user_turn_ids: tuple[str, ...],
    ) -> bool:
        captured = False
        for update in extraction.field_updates:
            if update.field not in ALL_FIELDS:
                continue  # unsupported field: rejected, not coerced
            if not update.source_turn_ids or not set(update.source_turn_ids) <= set(user_turn_ids):
                continue  # every fact must cite a real user turn
            value = clean_value(update.field, update.value)
            if value is None:
                continue  # unsupported value (e.g. an unknown opportunity type)
            if update.field == "priority" and not self._is_user_priority_choice(
                target, value, user_text
            ):
                continue  # priority is only ever the user's explicit choice
            captured |= self._set(s, update.field, value, target)
        return captured

    @staticmethod
    def _is_user_priority_choice(target: Target | None, value: Any, user_text: str) -> bool:
        return (
            target is not None
            and target.field == "priority"
            and value in PRIORITIES
            and str(value).casefold() in user_text.casefold()
        )

    @staticmethod
    def _set(s: InterviewState, fld: str, value: Any, target: Target | None) -> bool:
        existing = s.canvas.get(fld)
        if not existing:
            s.canvas[fld] = value
            s.unknown.discard(fld)
            return True
        if fld == "opportunityTypes":  # adding a type is not a contradiction
            merged = list(existing) + [v for v in value if v not in existing]
            changed = merged != existing
            s.canvas[fld] = merged
            return changed
        if str(existing).casefold() == str(value).casefold():
            return False
        # A different answer to an already-answered field: ask before replacing anything.
        s.conflicts[fld] = value
        return True

    @staticmethod
    def _resolve_conflict(s: InterviewState, fld: str, text: str) -> None:
        proposed = s.conflicts.pop(fld)
        low = text.casefold()
        if re.search(r"\b(first|earlier|original|old|before|keep)\b", low):
            return  # the user kept what they said before
        if re.search(r"\b(second|new|latest|now|latter|change)\b", low):
            s.canvas[fld] = proposed
            return
        s.canvas[fld] = clean_value(fld, text) or s.canvas[fld]

    @staticmethod
    def _confirm(s: InterviewState, fld: str, text: str, stance: Stance, override: bool) -> bool:
        if stance != "answered" or override:
            return False  # "yes", "that's all", "I don't know": nothing to add
        joined = clean_value(fld, f"{s.canvas.get(fld, '')}; {text}")
        if joined is None:
            return False
        s.canvas[fld] = joined
        return True

    @staticmethod
    def _note_non_answer(s: InterviewState, target: Target, stance: Stance) -> None:
        if target.kind == "optional":
            s.declined.add(target.field)
        else:  # uncertainty is kept as uncertainty; it never satisfies the validator
            s.unknown.add(target.field)
            s.attempts[target.field] = s.attempts.get(target.field, 0) + 1

    # -- composing the reply --------------------------------------------------------------------
    def _finish(self, s: InterviewState, cue: str | None, model_question: str | None) -> Outcome:
        nxt = choose_target(s)
        s.target = nxt
        if nxt is None:
            return Outcome(READY_TEXT, s, "ready_for_review")
        if nxt.kind == "optional":
            s.prompted.add(nxt.field)  # recorded only when this turn is committed (i.e. shown)
        question = render_question(
            nxt.kind,
            nxt.field,
            s.canvas,
            attempts=s.attempts.get(nxt.field, 0),
            conflict_new=s.conflicts.get(nxt.field),
        )
        if model_question and nxt.kind in ("missing", "clarify", "optional") and not s.conflicts:
            if validate_question(model_question, nxt.field):
                question = model_question.strip()  # a valid model question; otherwise the template
        text = f"{cue} {question}" if cue else question
        return Outcome(text, s, "clarify" if nxt.kind == "clarify" else "ask")


__all__ = [
    "Extraction",
    "ExtractionFailed",
    "ExtractionInput",
    "Extractor",
    "FieldUpdate",
    "InterviewState",
    "Interviewer",
    "OPPORTUNITY_TYPES",
    "Outcome",
    "Target",
    "canvas_view",
    "choose_target",
    "detect_stance",
    "is_ready",
    "looks_like_override",
]
