"""The one-page canvas as a PowerPoint file. All content is synthetic."""

import io

import pytest
from pptx import Presentation

from app.canvas_export import PPTX_MEDIA_TYPE, render_canvas_pptx
from tests.appendix.test_appendix_api import appendix as _appendix  # noqa: F401  (shared helpers)
from tests.appendix.test_appendix_api import finish, finish_confirmed
from tests.interview.conftest import OPENING, browser
from tests.interview.test_interview_api import history, wire

FIELDS = {
    "solutionName": "Shift Notes Assistant",
    "team": "Operations",
    "priority": "Must-have",
    "opportunityTypes": ["Find Information", "Generate Reports"],
    "currentProcess": "Supervisors copy shift notes between three spreadsheets.",
    "painPoints": "It takes hours; details get lost. Zoë’s café — Ωmega, Привет",
    "futureState": "Notes are captured once.",
    "achieve": "Faster, safer handover.",
    "success": "Handover takes minutes.",
    "kpis": "Hours saved per week.",
    "users": "Shift supervisors.",
    "decision": "Which issues to escalate.",
    "data": "Shift logs and incident records.",
    "systems": "The rostering system.",
    "risks": "Notes may contain <sensitive> & private details.",
}


def open_deck(data: bytes):
    return Presentation(io.BytesIO(data))


def slide_text(slide) -> str:
    return "\n".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)


def test_the_canvas_is_one_widescreen_slide_with_every_answer_on_it():
    deck = open_deck(render_canvas_pptx(FIELDS))
    assert len(deck.slides) == 1
    assert (deck.slide_width, deck.slide_height) == (12192000, 6858000)  # 16:9
    text = slide_text(deck.slides[0])
    for key, value in FIELDS.items():
        if key not in ("priority", "opportunityTypes"):
            assert value in text, key  # in full, special characters as written
    assert "AI OPPORTUNITY CANVAS" in text and "BHP" in text


def test_it_is_editable_text_and_shapes_not_a_picture():
    slide = open_deck(render_canvas_pptx(FIELDS)).slides[0]
    kinds = {shape.shape_type for shape in slide.shapes}
    assert 13 not in kinds  # MSO_SHAPE_TYPE.PICTURE
    panels = [s for s in slide.shapes if s.name.startswith("Panel ")]
    assert [s.name.split(":")[0] for s in panels] == [f"Panel {n}" for n in range(1, 6)]


def test_the_selected_priority_and_opportunity_types_are_marked():
    text = slide_text(open_deck(render_canvas_pptx(FIELDS)).slides[0])
    assert "■ Must-have" in text and "□ Nice-to-have" in text and "□ Exploratory" in text
    assert "■ Find Information" in text and "■ Generate Reports" in text
    assert "□ Predict Issues" in text and "□ Automate Workflows" in text


def test_an_empty_optional_answer_says_not_provided():
    text = slide_text(
        open_deck(render_canvas_pptx({**FIELDS, "kpis": "", "businessImpact": ""})).slides[0]
    )
    assert text.count("Not provided") == 2


def test_the_colours_are_the_canvas_colours():
    slide = open_deck(render_canvas_pptx(FIELDS)).slides[0]
    lines = {
        s.name.split(":")[0]: str(s.line.color.rgb)
        for s in slide.shapes
        if s.name.startswith("Panel ")
    }
    assert lines == {
        "Panel 1": "F37021",
        "Panel 2": "7B61FF",
        "Panel 3": "02B5E2",
        "Panel 4": "00C48C",
        "Panel 5": "F37021",
    }
    assert str(slide.background.fill.fore_color.rgb) == "1E1E1E"


def sizes(slide) -> list[float]:
    out = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            out += [
                run.font.size.pt
                for p in shape.text_frame.paragraphs
                for run in p.runs
                if run.font.size
            ]
    return out


def test_long_answers_shrink_to_fit_the_panel_and_stay_readable_on_one_slide():
    long = {
        k: ("A long answer sentence that goes on. " * 27)[:1000]
        for k in FIELDS
        if k not in ("priority", "opportunityTypes")
    }
    long.update(priority="Must-have", opportunityTypes=["Find Information"])
    deck = open_deck(render_canvas_pptx(long))
    assert len(deck.slides) == 1
    small, large = sizes(deck.slides[0]), sizes(open_deck(render_canvas_pptx(FIELDS)).slides[0])
    assert min(small) >= 6 and max(small) <= 30  # never unreadably small
    assert min(small) < min(large)  # the long answers did shrink
    assert "A long answer sentence" in slide_text(deck.slides[0])  # nothing was cut


def test_everything_sits_inside_the_slide():
    deck = open_deck(render_canvas_pptx(FIELDS))
    for shape in deck.slides[0].shapes:
        assert 0 <= shape.left and shape.left + shape.width <= deck.slide_width, shape.name
        assert 0 <= shape.top and shape.top + shape.height <= deck.slide_height, shape.name


def test_text_that_would_break_the_file_is_made_safe():
    deck = open_deck(render_canvas_pptx({**FIELDS, "team": "Ops\x00\x07 \ud800 team"}))
    assert "Ops" in slide_text(deck.slides[0])


def test_a_canvas_without_types_or_priority_still_renders():
    assert open_deck(render_canvas_pptx({"solutionName": "x"})).slides


# --- the download -----------------------------------------------------------------------------------


def canvas_file(client, fmt="pptx", revision=None):
    revision = history(client)["revision"] if revision is None else revision
    return client.get("/api/canvas-file", params={"format": fmt, "revision": revision})


def test_with_nothing_answered_there_is_nothing_to_download(world):
    wire(world)
    client = browser(world)
    response = canvas_file(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "nothing_to_download"


@pytest.mark.parametrize("fmt", ["pdf", "pptx"])
def test_an_unfinished_canvas_downloads_as_a_draft_with_the_answers_so_far(world, fmt):
    from tests.interview.test_interview_api import history, say

    wire(world)
    client = browser(world)
    say(client, OPENING)
    assert history(client)["canvas"]["ready_for_review"] is False
    response = canvas_file(client, fmt)
    assert response.status_code == 200 and response.content
    assert "-canvas-draft-r" in response.headers["content-disposition"]


def test_a_ready_canvas_downloads_as_a_deck_matching_the_stored_draft(world):
    wire(world)
    client = browser(world)
    data = finish_confirmed(client)
    response = canvas_file(client)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(PPTX_MEDIA_TYPE)
    assert response.headers["content-disposition"] == (
        f'attachment; filename="shift-notes-assistant-canvas-r{data["revision"]}.pptx"'
    )
    assert response.headers["cache-control"] == "no-store"
    text = slide_text(open_deck(response.content).slides[0])
    for key, value in data["canvas"]["fields"].items():
        for part in value if isinstance(value, list) else [value]:
            assert part in text, key
    assert history(client)["canvas"]["ready_for_review"]


def test_the_deck_needs_a_session_and_a_known_format(world):
    wire(world)
    client = browser(world)
    finish_confirmed(client)
    assert 400 <= canvas_file(client, "exe").status_code < 500
    assert (
        400 <= client.get("/api/canvas-file", params={"format": "pdf"}).status_code < 500
    )  # no revision
    revision = history(client)["revision"]
    client.cookies.clear()
    assert canvas_file(client, "pptx", revision).status_code == 401


def test_one_session_never_gets_anothers_deck(world):
    wire(world)
    owner, other = browser(world), browser(world)
    finish(owner)
    assert canvas_file(other).status_code == 409


@pytest.mark.parametrize("fmt", ["pdf", "docx", "json"])
def test_the_appendix_routes_are_unaffected(world, fmt):
    wire(world)
    client = browser(world)
    finish(client)
    assert _appendix(client, fmt).status_code == 200  # no confirmation needed to save it
