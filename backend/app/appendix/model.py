"""The immutable input every appendix format is rendered from."""

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.attachments.markers import typed_part
from app.conversation.store import Turn
from app.interview.canvas import LABELS

AnswerStatus = Literal["answered", "skipped", "not_provided"]
Speaker = Literal["user", "assistant"]

# The canvas sections, in the template's order, so the appendix reads like the one-pager. Data and
# systems get a section of their own because assessors need them in full.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Overview", ("solutionName", "team", "priority")),
    ("1. Current state", ("currentProcess", "painPoints", "businessImpact")),
    ("2. AI opportunity", ("opportunityTypes", "futureState")),
    ("3. Solution concept", ("achieve", "success")),
    ("4. Value and success", ("kpis",)),
    ("5. Data sources and systems", ("data", "systems")),
    ("6. Key assumptions and questions", ("users", "decision", "risks")),
)
OPTIONAL = frozenset({"businessImpact", "kpis"})


class AppendixError(Exception):
    """A typed failure: not_ready, changed or render_failed. Never carries user content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExportSnapshot:
    """What was stored at one moment: the conversation revision and the draft it produced."""

    revision: int
    canvas: dict[str, Any]
    declined: tuple[str, ...]
    turns: tuple[Turn, ...]
    confirmed_revision: int | None = None
    # False for an unfinished draft saved to carry on later (some required answers missing).
    complete: bool = True


@dataclass(frozen=True)
class Answer:
    field_id: str
    label: str
    status: AnswerStatus
    value: str | tuple[str, ...] | None


@dataclass(frozen=True)
class AnswerSection:
    title: str
    answers: tuple[Answer, ...]


@dataclass(frozen=True)
class QATurn:
    number: int
    speaker: Speaker
    text: str


@dataclass(frozen=True)
class Summary:
    """A quick read of the whole conversation. Worked out from the stored record by plain code
    (counts, and the start of each answer); no model writes or shortens anything."""

    facts: tuple[tuple[str, str], ...]
    key_answers: tuple[tuple[str, str], ...]
    overview: str | None = (
        None  # an AI-written paragraph from the conversation alone, when available
    )


@dataclass(frozen=True)
class AppendixInput:
    """Frozen copy of the confirmed revision: later changes cannot reach an export built from it."""

    source_revision: int
    summary: Summary
    sections: tuple[AnswerSection, ...]
    turns: tuple[QATurn, ...]
    app_name: str = "Plan on a Page"  # from configuration; shown in the document title
    draft: bool = False  # an unfinished canvas saved to carry on later

    @property
    def included_turns(self) -> int:
        return len(self.turns)


_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def clean_text(text: str) -> str:
    """Text that every format can hold: valid UTF-8, no control characters, plain newlines."""
    text = text.encode("utf-8", "replace").decode("utf-8")  # lone surrogates become '?'
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return _BAD_XML.sub("", text)


def _answer(field_id: str, canvas: dict[str, Any], declined: frozenset[str]) -> Answer:
    value = canvas.get(field_id)
    label = LABELS[field_id]
    if isinstance(value, list) and value:
        return Answer(field_id, label, "answered", tuple(clean_text(v) for v in value))
    if isinstance(value, str) and value.strip():
        return Answer(field_id, label, "answered", clean_text(value))
    status: AnswerStatus = (
        "skipped" if field_id in OPTIONAL and field_id in declined else "not_provided"
    )
    return Answer(field_id, label, status, None)


SHORT_ANSWER = 140  # characters of each answer shown in the summary; the full text is in Part A
SHORT_OPENING = 400


def shorten(text: str, limit: int) -> str:
    """The start of `text` on one line, cut at a word and marked with an ellipsis if it was cut."""
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    cut = one_line[:limit].rsplit(" ", 1)[0] or one_line[:limit]
    return cut.rstrip(" ,;:.") + "…"


def _summary(sections: tuple[AnswerSection, ...], turns: tuple[QATurn, ...]) -> Summary:
    answers = {a.field_id: a for section in sections for a in section.answers}

    def value(field_id: str, fallback: str = "Not provided") -> str:
        a = answers[field_id]
        return display_value(a) if a.value is not None else fallback

    name, team = value("solutionName"), value("team")
    skipped = [a.label for a in answers.values() if a.status == "skipped"]
    first_user = next((t for t in turns if t.speaker == "user"), None)
    users = sum(1 for t in turns if t.speaker == "user")
    facts = [
        ("Idea", f"{name} ({team})"),
        ("Priority", value("priority")),
        ("Opportunity types", value("opportunityTypes")),
    ]
    if first_user is not None:
        opening = typed_part(first_user.text) or first_user.text  # their own words first
        facts.append(("How it was first described", shorten(opening, SHORT_OPENING)))
    facts += [
        (
            "Conversation",
            f"{users} answers from the user and {len(turns) - users} replies from the assistant, "
            f"{len(turns)} messages in all.",
        ),
        ("Optional questions skipped", ", ".join(skipped) if skipped else "None"),
    ]
    key_answers = tuple(
        (
            a.label,
            shorten(display_value(a), SHORT_ANSWER) if a.value is not None else display_value(a),
        )
        for a in answers.values()
    )
    return Summary(tuple(facts), key_answers)


def build_input(snapshot: ExportSnapshot, app_name: str = "Plan on a Page") -> AppendixInput:
    declined = frozenset(snapshot.declined)
    sections = tuple(
        AnswerSection(title, tuple(_answer(f, snapshot.canvas, declined) for f in fields))
        for title, fields in SECTIONS
    )
    # Only turns the user could see: finished ones. Nothing but the stored text is ever included.
    shown = [t for t in snapshot.turns if t.status == "complete" and t.text.strip()]
    turns = tuple(
        QATurn(n, "user" if t.role == "user" else "assistant", clean_text(t.text))
        for n, t in enumerate(shown, start=1)
    )
    return AppendixInput(
        snapshot.revision,
        _summary(sections, turns),
        sections,
        turns,
        app_name,
        draft=not snapshot.complete,
    )


SPEAKER_LABELS = {"user": "User", "assistant": "Assistant"}
OMITTED_TEXT = {
    "skipped": "Not provided (the user chose to skip this question)",
    "not_provided": "Not provided",
}


def display_value(answer: Answer) -> str:
    """The answer as readable text, in full. A list is written as a comma-separated line."""
    if answer.value is None:
        return OMITTED_TEXT[answer.status]
    return ", ".join(answer.value) if isinstance(answer.value, tuple) else answer.value


INTRO = "Full answers and conversation record behind the one-page canvas"
SUMMARY_NOTE = (
    "The facts and key answers below are worked out by plain counting and the start of each "
    "answer. Nothing is interpreted; the full text of every answer is in Part A and every message "
    "is in Part B."
)
OVERVIEW_LABEL = "Overview (written by AI from this conversation only)"
OVERVIEW_NOTE = "Check it against Part B: where they differ, the conversation record is correct."
OVERVIEW_UNAVAILABLE = "An AI-written overview could not be prepared for this download."
DRAFT_NOTE = (
    "UNFINISHED DRAFT: some required answers are still missing. To carry on, start a new session "
    "and attach this file to your first answer."
)
SNAPSHOT_NOTE = "A snapshot of this revision: later changes to the conversation are not included."
RECORD_NOTE = (
    "Every message in the order it was sent. Corrections stay in this record; Part A shows the "
    "current answers."
)
