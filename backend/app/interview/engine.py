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

from app.attachments.markers import typed_part
from app.interview.canvas import (
    ALL_FIELDS,
    OPPORTUNITY_TYPES,
    OPTIONAL_FIELDS,
    PRIORITIES,
    Canvas,
    clean_value,
    missing_required,
    priority_key,
)
from app.interview.prompts import (
    AMEND_REPLIES,
    READY_TEXT,
    TIPS,
    acknowledgement,
    render_question,
    validate_question,
)

Kind = Literal["missing", "clarify", "confirm", "optional", "probe"]
Stance = Literal["answered", "unsure", "declined", "affirm"]
Phase = Literal["interview", "clarification", "data_and_systems", "review_ready"]


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
    probed: set[str] = field(default_factory=set)  # answers followed up once ("why is it High?")
    unknown: set[str] = field(default_factory=set)  # required fields the user said they don't know
    attempts: dict[str, int] = field(default_factory=dict)
    conflicts: dict[str, Any] = field(default_factory=dict)  # field -> new value awaiting a choice
    question: str | None = None  # the outstanding question as shown (without the acknowledgement)
    # The conversation revision the user confirmed in the read-back. It only ever applies to that
    # revision: any later answer moves the revision on, which ends the confirmation.
    confirmed_revision: int | None = None


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
_KEEP_EARLIER = re.compile(r"\b(first|earlier|original|old|before|keep)\b")
_TAKE_NEW = re.compile(r"\b(second|new|latest|now|latter|change)\b")
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
    if "priority" not in s.probed:  # one follow-up on the priority the user chose
        return Target("probe", "priority")
    for f in ("data", "systems"):  # dedicated passes, one per turn, even if already mentioned
        if f not in s.confirmed:
            return Target("confirm", f)
    for f in OPTIONAL_FIELDS:  # prompted separately; declining never blocks
        if f not in s.prompted and not s.canvas.get(f):  # an answered field is not asked again
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


# The fields whose answer is one (or several) of a fixed list. The server owns the lists, so the
# browser never has its own copy: it shows whatever is sent here.
CHOICE_FIELDS: dict[str, tuple[tuple[str, ...], bool]] = {
    "priority": (PRIORITIES, False),  # exactly one
    "opportunityTypes": (OPPORTUNITY_TYPES, True),  # one or more
}


def choices_for(target: Target, s: InterviewState) -> dict[str, Any] | None:
    """The options to offer for the outstanding question, or None when it needs typed words.

    Only when the question is asking for the field itself ("missing", or asking again because the
    answer was unclear). A follow-up (why that priority?) and a choice between an earlier and a new
    value are not about the list, so they are typed. Picking an option sends its wording as the
    user's answer, so every rule about typed words (the priority is only ever the user's own) holds.
    """
    spec = CHOICE_FIELDS.get(target.field)
    if spec is None:
        return None
    if target.kind == "missing" or (target.kind == "clarify" and target.field not in s.conflicts):
        options, multiple = spec
        return {"multiple": multiple, "options": list(options)}
    return None


def canvas_view(s: InterviewState) -> dict[str, Any]:
    """What the browser may see of the draft."""
    return {
        "fields": dict(s.canvas),
        "missing_required": missing_required(s.canvas),
        "ready_for_review": is_ready(s),
    }


def progression_view(s: InterviewState) -> dict[str, Any]:
    """Where the interview is, computed on the server and only ever shown by the browser."""
    t = s.target
    if is_ready(s):
        phase: Phase = "review_ready"
    elif t is not None and t.kind == "clarify":
        phase = "clarification"
    elif t is not None and t.kind == "confirm":
        phase = "data_and_systems"
    else:
        phase = "interview"
    reason = None
    if phase == "clarification" and t is not None:
        reason = (
            "conflict"
            if t.field in s.conflicts
            else "unsure"
            if t.field in s.unknown
            else "unclear"
        )
    return {
        "phase": phase,
        "outstanding_question": (
            {"target": t.field, "kind": t.kind, "text": s.question, "choices": choices_for(t, s)}
            if t is not None and s.question
            else None
        ),
        "clarification": {"active": phase == "clarification", "reason": reason},
        "validation": {"missing_required": missing_required(s.canvas), "unsure": sorted(s.unknown)},
        # The dedicated pass is tracked on its own: answering data/systems in passing never counts.
        "data_and_systems_complete": {"data", "systems"} <= s.confirmed,
        "priority_follow_up_asked": "priority" in s.probed,
        "optional_prompted": sorted(s.prompted),
        "optional_declined": sorted(s.declined),
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
        amending = is_ready(state)  # a finished canvas the user is coming back to correct
        stance = detect_stance(typed_part(user_text), target)  # only what they typed
        override = looks_like_override(user_text)
        cue: str | None = None  # a key of ACK: what happened with this answer

        if target is not None and target.kind == "confirm":
            captured = self._confirm(s, target.field, user_text, stance, override)
            s.confirmed.add(target.field)
            cue = "captured" if captured else "confirmed"
        elif target is not None and target.kind == "probe":
            cue = "captured" if self._probe(s, user_text, stance, override) else "declined"
        elif target is not None and target.kind == "clarify" and target.field in s.conflicts:
            cue = self._resolve_conflict(s, target.field, user_text, stance)
        elif target is not None and stance in ("unsure", "declined"):
            self._note_non_answer(s, target, stance)
            cue = "declined" if target.kind == "optional" else "unsure"
        else:
            extraction = self._extractor.extract(
                ExtractionInput(user_text, user_turn_id, user_turn_ids, target, s.canvas, override)
            )
            captured = self._apply(s, extraction, target, user_text, user_turn_ids, amending)
            if override:
                cue = "override"
            elif s.conflicts:
                cue = "conflict"
            elif captured:
                cue = "captured"
            else:  # nothing usable was captured
                if target is not None and target.kind == "optional":
                    s.declined.add(target.field)  # an optional answer we cannot use never blocks
                elif target is not None:
                    s.attempts[target.field] = s.attempts.get(target.field, 0) + 1
                cue = "nothing"
            return self._finish(s, cue, extraction.next_question, len(user_turn_ids), amending)

        return self._finish(s, cue, None, len(user_turn_ids), amending)

    # -- applying proposals ---------------------------------------------------------------------
    def _apply(
        self,
        s: InterviewState,
        extraction: Extraction,
        target: Target | None,
        user_text: str,
        user_turn_ids: tuple[str, ...],
        amending: bool = False,
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
                target,
                value,
                typed_part(user_text),  # never from an attached file
                amending,
            ):
                continue  # priority is only ever the user's explicit choice
            captured |= self._set(s, update.field, value, target)
        return captured

    @staticmethod
    def _is_user_priority_choice(
        target: Target | None, value: Any, user_text: str, amending: bool = False
    ) -> bool:
        if value not in PRIORITIES or priority_key(str(value)) not in priority_key(user_text):
            return False
        if target is not None and target.field == "priority":
            return True
        # Coming back to a finished canvas, the priority can be changed only when the user's own
        # words say "priority" and name the new one.
        return amending and target is None and "priority" in user_text.casefold()

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
    def _resolve_conflict(s: InterviewState, fld: str, text: str, stance: Stance) -> str:
        """Apply the user's choice between the earlier and the new answer; returns the cue key.

        Only a clear choice resolves it. Anything else leaves the conflict open so the same
        question is asked again, and nothing the user said is overwritten. After two unclear
        replies the earlier answer is kept (never replaced without clear consent), and the
        user is told.
        """
        old, proposed = str(s.canvas.get(fld, "")), str(s.conflicts[fld])
        low = text.casefold().strip()
        keep = bool(_KEEP_EARLIER.search(low))
        take = bool(_TAKE_NEW.search(low))
        if low == old.casefold():
            choice = "keep"
        elif low == proposed.casefold():
            choice = "take"
        elif stance in ("unsure", "declined") or keep == take:  # no choice, or both at once
            choice = None
        else:
            choice = "keep" if keep else "take"

        if choice is None:
            s.attempts[fld] = s.attempts.get(fld, 0) + 1
            if s.attempts[fld] < 2:
                return "unclear"
            del s.conflicts[fld]
            return "kept_earlier"
        proposed_value = s.conflicts.pop(fld)
        if choice == "take":
            s.canvas[fld] = proposed_value
        return "captured"

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
    def _probe(s: InterviewState, text: str, stance: Stance, override: bool) -> bool:
        """The reason behind the chosen priority is the business case, so it goes in Business
        Impact (joined to anything already there). Skipping it never blocks anything."""
        if stance != "answered" or override:
            return False
        existing = s.canvas.get("businessImpact")
        joined = clean_value("businessImpact", f"{existing}; {text}" if existing else text)
        if joined is None:
            return False
        s.canvas["businessImpact"] = joined
        return True

    @staticmethod
    def _note_non_answer(s: InterviewState, target: Target, stance: Stance) -> None:
        if target.kind == "optional":
            s.declined.add(target.field)
        else:  # uncertainty is kept as uncertainty; it never satisfies the validator
            s.unknown.add(target.field)
            s.attempts[target.field] = s.attempts.get(target.field, 0) + 1

    # -- composing the reply --------------------------------------------------------------------
    def _finish(
        self,
        s: InterviewState,
        cue: str | None,
        model_question: str | None,
        turn_number: int,
        amending: bool = False,
    ) -> Outcome:
        nxt = choose_target(s)
        s.target = nxt
        if nxt is None:
            s.question = None
            if amending and cue in AMEND_REPLIES:  # said something after "that's everything"
                return Outcome(AMEND_REPLIES[cue], s, "ready_for_review")
            return Outcome(READY_TEXT, s, "ready_for_review")
        if nxt.kind == "optional":
            s.prompted.add(nxt.field)  # recorded only when this turn is committed (i.e. shown)
        if nxt.kind == "probe":
            s.probed.add(nxt.field)
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
        s.question = question
        first_ask = nxt.kind in ("missing", "optional", "probe") and not s.attempts.get(nxt.field)
        tip = TIPS.get("probe" if nxt.kind == "probe" else nxt.field) if first_ask else None
        parts = [acknowledgement(cue, turn_number) if cue else None, question, tip]
        text = " ".join(p for p in parts if p)
        return Outcome(text, s, "clarify" if nxt.kind == "clarify" else "ask")


__all__ = [
    "Extraction",
    "ExtractionFailed",
    "ExtractionInput",
    "Extractor",
    "FieldUpdate",
    "InterviewState",
    "Interviewer",
    "Phase",
    "OPPORTUNITY_TYPES",
    "Outcome",
    "Target",
    "canvas_view",
    "choose_target",
    "detect_stance",
    "is_ready",
    "looks_like_override",
    "progression_view",
]
