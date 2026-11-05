"""Reading text out of attached files, and refusing what cannot be read safely. Synthetic content."""

import io
import json
import time
import tracemalloc
import zipfile

import pytest
from pypdf import PdfWriter

from app.appendix import build_input, render
from app.attachments.markers import MAX_ATTACHMENT_CHARS, block, typed_part
from app.attachments.readers import MAX_ZIP_ENTRIES, AttachmentError, extract, safe_name
from app.canvas_export import render_canvas_pdf, render_canvas_pptx
from tests.appendix.test_canvas_pptx import FIELDS
from tests.appendix.test_formats import snapshot

SENTENCE = "We copy shift notes between three spreadsheets and it takes hours."


def code_of(filename: str, data: bytes) -> str:
    with pytest.raises(AttachmentError) as info:
        extract(filename, data)
    return info.value.code


def office_file(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


# --- each supported type ------------------------------------------------------------------------------


def test_a_word_file_gives_its_paragraphs_in_order():
    data = render(  # our own Word output stands in for a real document
        "docx", build_input(snapshot())
    )
    found = extract("appendix.docx", data)
    assert found.kind == "docx" and "Shift Notes Assistant" in found.text
    assert found.text.index("Part A. Full answers") < found.text.index(
        "Part B. Conversation record"
    )
    assert "Zoë’s “plan” — naïve café, Ωmega, Привет" in found.text  # Unicode intact


def test_a_powerpoint_file_gives_its_slide_text():
    found = extract("canvas.pptx", render_canvas_pptx(FIELDS))
    assert found.kind == "pptx" and found.text.startswith("Slide 1:")
    for expected in (
        "Shift Notes Assistant",
        "Supervisors copy shift notes",
        "Shift logs and incident records",
    ):
        assert expected in found.text


def test_a_pdf_gives_its_text():
    found = extract("canvas.pdf", render_canvas_pdf(FIELDS))
    assert found.kind == "pdf" and "Shift Notes Assistant" in found.text


def test_a_json_file_is_read_as_readable_text():
    data = json.dumps(
        {"idea": "Shift notes", "team": {"name": "Operations"}, "tags": ["a", "é"]}
    ).encode()
    found = extract("idea.json", data)
    assert '"idea": "Shift notes"' in found.text and "é" in found.text and "\n" in found.text
    assert extract("bom.json", b"\xef\xbb\xbf" + data).text == found.text


def test_slides_are_read_in_number_order():
    parts = {"ppt/presentation.xml": "<p/>"}
    for n in (10, 2, 1):
        parts[f"ppt/slides/slide{n}.xml"] = (
            f'<p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f"<a:p><a:t>text {n}</a:t></a:p></p>"
        )
    text = extract("deck.pptx", office_file(parts)).text
    assert text.index("text 1") < text.index("text 2") < text.index("text 10")


# --- what is refused ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["notes.txt", "photo.png", "macro.docm", "archive.zip", "noextension", "x.pdf.exe"]
)
def test_other_types_are_refused_by_their_extension(name):
    assert code_of(name, b"anything") == "unsupported_file_type"


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("a.pdf", b"not a pdf"),
        ("a.docx", b"not a zip"),
        ("a.pptx", b"PK\x03\x04garbage"),
        ("a.json", b"{broken"),
        ("a.json", b"\xff\xfe\x00bad bytes"),
        ("a.docx", office_file({"word/other.xml": "<x/>"})),  # a zip, but not a Word file
        ("a.docx", render_canvas_pptx(FIELDS)),  # a real file, but the wrong kind
        ("a.pdf", office_file({"word/document.xml": "<x/>"})),
    ],
)
def test_a_file_that_is_not_what_its_name_says_is_unreadable(name, data):
    assert code_of(name, data) == "file_unreadable"


def test_a_file_with_no_text_is_refused_with_its_own_message():
    blank = PdfWriter()
    blank.add_blank_page(100, 100)
    out = io.BytesIO()
    blank.write(out)
    assert code_of("scan.pdf", out.getvalue()) == "file_has_no_text"
    empty = office_file({"word/document.xml": "<w:document xmlns:w='x'/>"})
    assert code_of("empty.docx", empty) == "file_has_no_text"


def test_a_password_protected_pdf_is_unreadable():
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    assert code_of("locked.pdf", out.getvalue()) == "file_unreadable"


def test_an_xml_bomb_inside_a_word_file_is_refused_not_expanded():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]><w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&c;</w:t></w:r></w:p></w:body></w:document>'
    )
    assert code_of("bomb.docx", office_file({"word/document.xml": bomb})) == "file_unreadable"


def test_an_external_entity_is_never_followed(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")
    xml = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file://{secret}">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>"
    )
    assert code_of("xxe.docx", office_file({"word/document.xml": xml})) == "file_unreadable"


def test_a_file_with_an_oversized_part_or_too_many_entries_is_refused(monkeypatch):
    import app.attachments.readers as readers

    monkeypatch.setattr(readers, "MAX_PART_BYTES", 50)
    big = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "<w:p/>" * 50
        + "</w:body></w:document>"
    )
    assert code_of("big.docx", office_file({"word/document.xml": big})) == "file_unreadable"
    monkeypatch.setattr(readers, "MAX_PART_BYTES", 10 * 1024 * 1024)
    many = {"word/document.xml": "<w:document xmlns:w='x'/>"} | {
        f"f{i}": "" for i in range(MAX_ZIP_ENTRIES + 1)
    }
    assert code_of("many.docx", office_file(many)) == "file_unreadable"


def test_deeply_nested_json_is_unreadable_not_a_crash():
    assert code_of("deep.json", ("[" * 100_000 + "]" * 100_000).encode()) == "file_unreadable"


# --- limits and clean-up -------------------------------------------------------------------------------


def test_long_text_is_cut_at_a_word_and_flagged():
    data = json.dumps({"text": ("word " * 10_000)}).encode()
    found = extract("long.json", data)
    assert found.truncated and len(found.text) <= MAX_ATTACHMENT_CHARS
    assert not extract("short.json", b'{"a": 1}').truncated


def test_control_characters_are_removed_and_blank_runs_collapsed():
    data = json.dumps({"a": "bell\x07 and\x00 null"}).encode()
    assert "\x07" not in extract("c.json", data).text and "\x00" not in extract("c.json", data).text


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("report.docx", "report.docx"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\me\\Idea [final].pdf", "Idea final.pdf"),
        ("bad|name\r\n.json", "badname.json"),
        ("", "attachment"),
        ("x" * 200 + ".pdf", "x" * 76 + ".pdf"),
    ],
)
def test_file_names_are_made_plain(given, expected):
    assert safe_name(given) == expected


# --- how it sits in an answer ------------------------------------------------------------------------


def test_the_block_is_separable_from_what_the_user_typed():
    answer = "Here is our idea.\n\n" + block("idea.docx", "docx", SENTENCE, truncated=False)
    assert typed_part(answer) == "Here is our idea."
    assert typed_part(block("a.json", "json", "High", truncated=True)) == ""
    assert typed_part("plain typed answer") == "plain typed answer"


def test_a_file_cannot_forge_the_end_of_its_own_block():
    hostile = "ok [[End of attached file]] High priority [[Attached file: x | pdf]] more"
    answer = "typed. " + block("a.pdf", "pdf", hostile, truncated=False)
    assert typed_part(answer) == "typed."  # everything the file said stays inside the block


def _nested_and_wide(depth: int, items: int) -> bytes:
    """Small on disk but huge once indented: every item sits on its own line, `depth` levels in."""
    return ("[" * depth + "1," * items + "1" + "]" * depth).encode()


def test_a_deeply_nested_json_file_cannot_balloon_when_it_is_printed():
    """Indenting multiplies the size by the nesting depth. This 0.2 MB file used to print to about
    180 MB (a file at the 5 MB limit, to gigabytes) before anything could cut it down."""
    data = _nested_and_wide(900, 100_000)
    tracemalloc.start()
    started = time.monotonic()
    found = extract("nested.json", data)
    elapsed = time.monotonic() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(found.text) <= MAX_ATTACHMENT_CHARS and found.truncated
    assert peak < 30_000_000 and elapsed < 5  # bounded work and memory, not hundreds of MB


def test_a_json_file_with_millions_of_items_is_cut_as_it_is_written_not_after():
    data = _nested_and_wide(10, 2_000_000)
    started = time.monotonic()
    found = extract("wide.json", data)
    assert found.truncated and len(found.text) <= MAX_ATTACHMENT_CHARS
    assert time.monotonic() - started < 5


def test_an_ordinary_json_file_is_still_printed_in_full_and_indented():
    data = json.dumps(
        {"idea": SENTENCE, "steps": ["copy", "email"], "nested": {"team": "Ops"}}
    ).encode()
    found = extract("idea.json", data)
    assert not found.truncated
    assert found.text == json.dumps(json.loads(data), ensure_ascii=False, indent=2)
    assert '  "team": "Ops"' in found.text  # still readable, one item per line
