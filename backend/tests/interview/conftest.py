"""Helpers for the interview tests. All content here is synthetic."""

import re

from app.interview.canvas import OPPORTUNITY_TYPES, PRIORITIES
from app.interview.engine import (
    Extraction,
    ExtractionInput,
    FieldUpdate,
    Interviewer,
    InterviewState,
)
from tests.access.conftest import csrf, signed_in, world  # noqa: F401  (fixtures for these tests)
from tests.ai.conftest import config_a, config_b  # noqa: F401
from tests.conversation.conftest import browser  # noqa: F401


class AnswerFiler:
    """TEST DOUBLE standing in for the model. Files a reply under the field being asked about.

    Lets the deterministic engine be driven through whole conversations without calling AWS. It is
    not part of the application and is never importable from `app`.
    """

    def extract(self, inp: ExtractionInput) -> Extraction:
        text = inp.user_text.strip()
        if not text or inp.override_attempt:
            return Extraction()
        source = [inp.user_turn_id]
        if inp.target is None:
            return Extraction([FieldUpdate("achieve", text, source)])
        fld = inp.target.field
        if fld == "opportunityTypes":
            found = [t for t in OPPORTUNITY_TYPES if t.casefold() in text.casefold()]
            return Extraction([FieldUpdate(fld, found, source)] if found else [])
        if fld == "priority":
            found = [p for p in PRIORITIES if re.search(rf"\b{p}\b", text, re.IGNORECASE)]
            return Extraction([FieldUpdate(fld, found[0], source)] if len(found) == 1 else [])
        return Extraction([FieldUpdate(fld, text, source)])


class FakeExtractor:
    """Returns queued extractions, like a model would, and records what it was asked."""

    def __init__(self, *extractions: Extraction) -> None:
        self.queue = list(extractions)
        self.inputs: list[ExtractionInput] = []

    def extract(self, inp: ExtractionInput) -> Extraction:
        self.inputs.append(inp)
        return self.queue.pop(0) if self.queue else Extraction()


class Conversation:
    """Drives an Interviewer turn by turn, minting user turn ids like the real conversation."""

    def __init__(self, extractor=None) -> None:
        self.interviewer = Interviewer(extractor or AnswerFiler())
        self.state = InterviewState()
        self.user_ids: list[str] = []
        self.replies: list[str] = []

    def say(self, text: str):
        self.user_ids.append(f"u{len(self.user_ids) + 1}")
        outcome = self.interviewer.process(
            self.state, text, self.user_ids[-1], tuple(self.user_ids)
        )
        self.state = outcome.state  # committed, as it is once the assistant turn is stored
        self.replies.append(outcome.text)
        return outcome

    @property
    def last_id(self) -> str:
        return self.user_ids[-1]


def update(field: str, value, turn_id: str = "u1") -> FieldUpdate:
    return FieldUpdate(field, value, [turn_id])


# One realistic synthetic answer per question the interviewer asks.
ANSWERS = {
    "solutionName": "Shift Notes Assistant",
    "team": "Operations",
    "currentProcess": "Supervisors copy notes between three spreadsheets each shift.",
    "painPoints": "It takes hours and details get lost.",
    "futureState": "Notes are captured once and everyone sees the same summary.",
    "opportunityTypes": "Find Information and Generate Reports",
    "success": "Handover takes minutes and nothing is missed.",
    "users": "Shift supervisors.",
    "decision": "Which issues to escalate at handover.",
    "data": "Shift logs and incident records.",
    "systems": "The rostering system.",
    "risks": "Notes may contain sensitive details.",
    "priority": "Must-have",
}
OPENING = "We copy shift notes between three spreadsheets and it takes hours."


def reply_to(target, default="Yes, that's all.") -> str:
    """A synthetic reply to whatever the interviewer is asking. The priority follow-up is declined
    here so tests that are about something else stay focused."""
    if target.kind == "probe":
        return "skip"
    return ANSWERS.get(target.field, default)
