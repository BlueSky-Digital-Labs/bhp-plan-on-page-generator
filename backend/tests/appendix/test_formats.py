"""The three appendix formats, rendered from the same frozen input. All content is synthetic."""

import copy
import dataclasses
import io
import json
import re
import zipfile
from xml.etree import ElementTree

import pytest
from pypdf import PdfReader

from app.appendix import AppendixError, ExportSnapshot, build_input, render
from app.appendix.model import clean_text
from app.conversation.store import Turn

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
LONG_TOKEN = "Z" * 400  # one unbroken string, wider than any line
UNICODE = "Zoë’s “plan” — naïve café, Ωmega, Привет, 100% ≥ 99"

FIELDS = {
    "solutionName": "Shift Notes Assistant",
    "team": "Operations",
    "priority": "Must-have",
    "opportunityTypes": ["Find Information", "Generate Reports"],
    "currentProcess": "Supervisors copy notes between three spreadsheets.\nThen they email a summary.",
    "painPoints": UNICODE,
    "futureState": "Notes are captured once.",
    "achieve": "Faster, safer handover.",
    "success": "Handover takes minutes.",
    "users": "Shift supervisors.",
    "decision": "Which issues to escalate.",
    "data": ("Shift logs, incident records and the safety register. " * 30).strip(),
    "systems": "The rostering system and the safety system.",
    "risks": "Notes may contain <sensitive> & private details.",
}


def turn(n: int, text: str, status: str = "complete") -> Turn:
    return Turn(f"{n:032x}", "user" if n % 2 else "assistant", text, status)


def conversation(count: int = 60) -> tuple[Turn, ...]:
    out = []
    for n in range(1, count + 1):
        body = f"Message {n:03d}: " + (
            "The shift notes are copied by hand. " * (6 if n % 7 else 40)
        )
        if n == 5:
            body += LONG_TOKEN
        if n == 9:
            body = f"Message 009: {UNICODE}"
        out.append(turn(n, body))
    return tuple(out)


def snapshot(turns=None, **over) -> ExportSnapshot:
    base = dict(
        revision=12, canvas=copy.deepcopy(FIELDS), declined=("businessImpact", "kpis"), turns=turns
    )
    base.update(over)
    base["turns"] = conversation() if base["turns"] is None else base["turns"]
    return ExportSnapshot(**base)


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def pdf_pages(data: bytes, *, keep_chrome: bool = False) -> list[str]:
    """Each page's text. The running header and the page-number footer are removed unless asked
    for, because a paragraph that crosses a page break has them in the middle of it."""
    pages = [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]
    if keep_chrome:
        return pages
    chrome = re.compile(r"^(Page \d+ of \d+|SCP AI Ideation assistant: Appendix · revision \d+)$")
    return ["\n".join(ln for ln in p.split("\n") if not chrome.match(ln.strip())) for p in pages]


def docx_paragraphs(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for p in root.iter(f"{W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{W}t":
                parts.append(node.text or "")
            elif node.tag == f"{W}br":
                parts.append("\n")
        paragraphs.append("".join(parts))
    return paragraphs


# --- the frozen input -----------------------------------------------------------------------------


def test_the_input_is_frozen_and_a_copy_so_later_changes_cannot_reach_an_export():
    source = snapshot()
    appendix = build_input(source)
    before = render("json", appendix)
    source.canvas["team"] = "Finance"  # a later amendment to the live draft
    source.canvas["opportunityTypes"].append("Predict Issues")
    assert render("json", appendix) == before  # the confirmed revision is unchanged
    with pytest.raises(dataclasses.FrozenInstanceError):
        appendix.source_revision = 99  # type: ignore[misc]


def test_only_finished_turns_are_in_the_record_and_each_keeps_its_place():
    turns = (
        turn(1, "First answer"),
        turn(2, "First question"),
        turn(3, "Second answer"),
        turn(4, "", "pending"),  # no text yet
        turn(5, "never shown", "failed"),
    )
    appendix = build_input(snapshot(turns))
    assert [(t.number, t.speaker, t.text) for t in appendix.turns] == [
        (1, "user", "First answer"),
        (2, "assistant", "First question"),
        (3, "user", "Second answer"),
    ]
    assert appendix.included_turns == 3


def test_clean_text_keeps_content_and_drops_only_what_a_document_cannot_hold():
    assert clean_text("a\r\nb\rc\td") == "a\nb\nc    d"
    assert clean_text("bell\x07 and null\x00 gone") == "bell and null gone"
    assert clean_text("lone \ud800 surrogate") == "lone ? surrogate"
    assert clean_text(UNICODE) == UNICODE


# --- JSON -----------------------------------------------------------------------------------------


def test_json_carries_every_answer_in_full_and_the_whole_record_in_order():
    data = json.loads(render("json", build_input(snapshot())))
    assert (data["format"], data["version"], data["source_revision"]) == (
        "plan-on-a-page-appendix",
        1,
        12,
    )
    answers = {a["id"]: a for s in data["sections"] for a in s["answers"]}
    assert answers["data"]["value"] == FIELDS["data"]  # in full, not condensed
    assert answers["opportunityTypes"]["value"] == ["Find Information", "Generate Reports"]
    assert answers["priority"]["value"] == "Must-have"
    assert answers["painPoints"]["value"] == UNICODE  # not escaped away
    assert [t["turn"] for t in data["conversation"]] == list(range(1, 61))
    assert data["included_turns"] == 60


def test_optional_omissions_are_explicit_not_silently_missing():
    canvas = {k: v for k, v in FIELDS.items()}
    data = json.loads(render("json", build_input(snapshot(canvas=canvas, declined=("kpis",)))))
    answers = {a["id"]: a for s in data["sections"] for a in s["answers"]}
    assert answers["kpis"]["status"] == "skipped" and answers["kpis"]["value"] is None
    assert answers["businessImpact"]["status"] == "not_provided"
    assert answers["team"]["status"] == "answered"


# --- PDF ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return render("pdf", build_input(snapshot()))


def test_the_pdf_spans_as_many_pages_as_needed_each_numbered(pdf_bytes):
    pages = pdf_pages(pdf_bytes, keep_chrome=True)
    assert len(pages) >= 8
    for number, text in enumerate(pages, start=1):
        assert f"Page {number} of {len(pages)}" in text


def test_the_pdf_keeps_every_answer_and_every_turn_with_nothing_cut_at_a_page_break(pdf_bytes):
    text = squash("".join(pdf_pages(pdf_bytes)))
    for expected in [FIELDS["data"], FIELDS["currentProcess"], UNICODE, LONG_TOKEN]:
        assert squash(expected) in text, expected[:40]
    for turn_ in conversation():
        assert squash(turn_.text) in text, turn_.text[:30]  # whole turns, across page boundaries


def test_the_pdf_lists_the_turns_in_the_order_they_were_sent(pdf_bytes):
    text = "".join(pdf_pages(pdf_bytes))
    positions = [text.index(f"Message {n:03d}:") for n in range(1, 61)]
    assert positions == sorted(positions)
    assert text.index("Part A. Full answers") < text.index("Part B. Conversation record")
    assert "Turn 1 · Assistant" in text or "Turn 1 · User" in text


def test_the_pdf_labels_what_the_user_skipped_and_says_which_revision_it_is(pdf_bytes):
    text = "".join(pdf_pages(pdf_bytes))
    assert "the user chose to skip this question" in text
    assert "Source revision 12" in text and "60 conversation turns" in text


def test_a_conversation_of_one_turn_and_an_empty_one_still_render():
    assert pdf_pages(render("pdf", build_input(snapshot(turns=(turn(1, "Only message"),)))))
    assert pdf_pages(render("pdf", build_input(snapshot(turns=()))))


# --- Word -----------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def docx_bytes() -> bytes:
    return render("docx", build_input(snapshot()))


def test_the_word_file_is_a_well_formed_package(docx_bytes):
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert {
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
            "word/footer1.xml",
        } <= names
        for name in names:
            ElementTree.fromstring(archive.read(name))  # every part is valid XML
        assert "NUMPAGES" in archive.read("word/footer1.xml").decode()  # page numbers


def test_the_word_file_keeps_every_answer_and_turn_in_order_with_special_characters(docx_bytes):
    paragraphs = docx_paragraphs(docx_bytes)
    text = "\n".join(paragraphs)
    for expected in [
        FIELDS["data"],
        FIELDS["currentProcess"],
        UNICODE,
        LONG_TOKEN,
        FIELDS["risks"],
    ]:
        assert expected in text, expected[:40]  # '<', '&' and line breaks arrive as written
    positions = [text.index(f"Message {n:03d}:") for n in range(1, 61)]
    assert positions == sorted(positions)
    assert text.index("Part A. Full answers") < text.index("Part B. Conversation record")


def test_the_word_file_is_the_same_bytes_every_time(docx_bytes):
    assert render("docx", build_input(snapshot())) == docx_bytes


def test_text_that_would_break_the_document_is_made_safe():
    nasty = "a\x00b\x07c </w:t><w:p> & ]]> \ud800"
    appendix = build_input(snapshot(turns=(turn(1, nasty),)))
    for fmt in ("pdf", "docx", "json"):
        assert render(fmt, appendix)
    assert "a" in "\n".join(docx_paragraphs(render("docx", appendix)))


def test_a_renderer_failure_is_a_typed_error_without_content(monkeypatch, caplog):
    import app.appendix as module

    def boom(_):
        raise RuntimeError("SECRET-IDEA-TEXT")

    monkeypatch.setitem(module._RENDERERS, "pdf", boom)
    with caplog.at_level("ERROR", logger="app.appendix"), pytest.raises(AppendixError) as info:
        render("pdf", build_input(snapshot()))
    assert info.value.code == "render_failed" and "SECRET-IDEA-TEXT" not in caplog.text


# --- the summary ------------------------------------------------------------------------------------


def test_the_summary_reads_the_conversation_by_plain_counting_and_the_start_of_each_answer():
    appendix = build_input(snapshot())
    facts = dict(appendix.summary.facts)
    assert facts["Idea"] == "Shift Notes Assistant (Operations)"
    assert facts["Priority"] == "Must-have"
    assert facts["Opportunity types"] == "Find Information, Generate Reports"
    assert (
        facts["Conversation"]
        == "30 answers from the user and 30 replies from the assistant, 60 messages in all."
    )
    assert facts["Optional questions skipped"] == "Business Impact, Expected Value/KPIs"
    assert facts["How it was first described"].startswith("Message 001:")


def test_long_answers_are_cut_in_the_summary_only_and_say_so_while_part_a_keeps_them_whole():
    appendix = build_input(snapshot())
    key = dict(appendix.summary.key_answers)
    assert key["What data is needed?"].endswith("…") and len(key["What data is needed?"]) <= 141
    assert key["Team"] == "Operations"  # short answers are untouched
    text = squash("".join(pdf_pages(render("pdf", appendix))))
    assert squash(FIELDS["data"]) in text  # the whole answer is still in Part A


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("short", 10, "short"),
        ("one two three four five", 12, "one two…"),
        ("a" * 50, 10, "a" * 10 + "…"),  # no space to cut at
        ("  spaced \n out\ttext ", 50, "spaced out text"),
    ],
)
def test_shorten_cuts_at_a_word_and_marks_the_cut(text, limit, expected):
    from app.appendix.model import shorten

    assert shorten(text, limit) == expected


def test_the_summary_is_the_same_every_time_and_needs_no_model():
    assert build_input(snapshot()).summary == build_input(snapshot()).summary
    import app.appendix.model as module

    assert "app.ai" not in {m.__name__ for m in vars(module).values() if hasattr(m, "__name__")}


def test_an_empty_conversation_still_has_a_summary():
    summary = dict(build_input(snapshot(turns=())).summary.facts)
    assert "How it was first described" not in summary
    assert summary["Conversation"].startswith("0 answers")


def test_the_summary_comes_before_the_full_answers_in_every_format(pdf_bytes, docx_bytes):
    pdf = "".join(pdf_pages(pdf_bytes))
    assert (
        pdf.index("Summary")
        < pdf.index("Part A. Full answers")
        < pdf.index("Part B. Conversation record")
    )
    word = "\n".join(docx_paragraphs(docx_bytes))
    assert word.index("Summary") < word.index("Part A. Full answers")
    assert (
        "Shift Notes Assistant (Operations)" in pdf and "Shift Notes Assistant (Operations)" in word
    )
    data = json.loads(render("json", build_input(snapshot())))
    assert list(data).index("summary") < list(data).index("sections")
    assert {f["label"] for f in data["summary"]["facts"]} >= {"Idea", "Priority", "Conversation"}
