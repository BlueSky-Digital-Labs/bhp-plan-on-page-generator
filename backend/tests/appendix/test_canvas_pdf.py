"""The one-page canvas as a PDF drawn by the server. All content is synthetic."""

import io
import re

from pypdf import PdfReader

from app.canvas_export import render_canvas_pdf
from tests.appendix.test_canvas_pptx import FIELDS


def read(fields):
    return PdfReader(io.BytesIO(render_canvas_pdf(fields)))


def text_of(reader) -> str:
    return " ".join(" ".join(p.extract_text() for p in reader.pages).split())


def test_it_is_one_a4_landscape_page_with_every_answer_on_it():
    reader = read(FIELDS)
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert (round(float(box.width)), round(float(box.height))) == (842, 595)  # A4 landscape
    text = text_of(reader)
    for key, value in FIELDS.items():
        if key not in ("priority", "opportunityTypes"):
            assert " ".join(value.split()) in text, key  # in full, special characters as written
    assert "OPPORTUNITY CANVAS" in text and "BHP" in text


def test_the_selected_priority_and_opportunity_types_are_marked():
    text = text_of(read(FIELDS))
    assert "■ Must-have" in text and "□ Nice-to-have" in text and "□ Exploratory" in text
    assert "■ Find Information" in text and "■ Generate Reports" in text
    assert "□ Predict Issues" in text and "□ Automate Workflows" in text


def test_an_empty_optional_answer_says_not_provided():
    text = text_of(read({**FIELDS, "kpis": "", "businessImpact": ""}))
    assert text.count("Not provided") == 2


def test_it_uses_the_canvas_background_and_panel_colours():
    content = read(FIELDS).pages[0].get_contents().get_data().decode("latin1")
    used = {
        tuple(round(float(v) * 255) for v in match[:3])
        for match in re.findall(r"([\d.]+) ([\d.]+) ([\d.]+) (rg|RG)", content)
    }
    for hex_colour in ("1E1E1E", "F37021", "7B61FF", "02B5E2", "00C48C"):
        assert tuple(int(hex_colour[i : i + 2], 16) for i in (0, 2, 4)) in used, hex_colour


def test_long_answers_stay_on_one_page_and_are_never_dropped_silently():
    sentence = "A long answer sentence that keeps going and going. "
    long = {k: (sentence * 25)[:1000] for k in FIELDS if k not in ("priority", "opportunityTypes")}
    long.update(priority="Must-have", opportunityTypes=["Find Information"])
    reader = read(long)
    assert len(reader.pages) == 1
    assert "A long answer sentence" in text_of(reader)


def test_an_absurdly_long_answer_is_cut_with_an_ellipsis_rather_than_spilling_off_the_page():
    huge = {
        **FIELDS,
        **{k: ("word " * 200).strip() for k in ("data", "systems", "risks", "users", "decision")},
    }
    reader = read(huge)
    assert len(reader.pages) == 1
    assert "…" in text_of(reader)


def test_a_very_long_solution_name_is_shortened_in_the_header_only():
    text = text_of(read({**FIELDS, "solutionName": "N" * 300}))
    assert "N" * 61 not in text and "…" in text


def test_unicode_is_kept_and_unusable_characters_do_not_break_it():
    reader = read({**FIELDS, "team": "Ops\x00\x07 \ud800 team Ωmega Привет café"})
    assert "Ωmega Привет café" in text_of(reader)


def test_a_canvas_with_almost_nothing_still_renders():
    assert len(read({"solutionName": "x"}).pages) == 1


def test_the_same_canvas_gives_the_same_page():
    assert text_of(read(FIELDS)) == text_of(read(FIELDS))
