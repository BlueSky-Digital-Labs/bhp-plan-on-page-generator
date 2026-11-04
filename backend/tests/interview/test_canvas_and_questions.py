import pytest

from app.interview.canvas import (
    OPPORTUNITY_TYPES,
    PRIORITIES,
    REQUIRED_ORDER,
    clean_value,
    missing_required,
)
from app.interview.prompts import (
    CONFIRM,
    CONFLICT,
    PROMPT_VERSION,
    QUESTIONS,
    render_question,
    validate_question,
)


def test_the_canvas_fields_match_the_frontend_definition():
    # frontend/src/canvas/fields.ts: 14 required (12 text + priority + opportunity type).
    assert len(REQUIRED_ORDER) == 14
    assert set(REQUIRED_ORDER) >= {"solutionName", "team", "priority", "opportunityTypes", "risks"}
    assert OPPORTUNITY_TYPES == (
        "Find Information",
        "Generate Reports",
        "Predict Issues",
        "Recommend Actions",
        "Analyse Data",
        "Automate Workflows",
    )
    assert PRIORITIES == ("Low", "Medium", "High", "Strategic")


def test_business_impact_and_kpis_are_never_required():
    assert {"businessImpact", "kpis"}.isdisjoint(REQUIRED_ORDER)
    assert set(missing_required({})) == set(REQUIRED_ORDER)


def test_missing_priority_or_opportunity_type_always_blocks_readiness():
    complete = {f: "x" for f in REQUIRED_ORDER}
    complete["opportunityTypes"] = ["Analyse Data"]
    assert missing_required(complete) == []
    assert missing_required({**complete, "priority": ""}) == ["priority"]
    assert missing_required({**complete, "opportunityTypes": []}) == ["opportunityTypes"]


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("team", "  Operations \n team ", "Operations team"),
        ("team", "", None),
        ("team", "   ", None),
        ("team", 42, None),
        ("team", "x" * 1001, None),
        ("budget", "anything", None),  # not a canvas field
        ("priority", "high", "High"),
        ("priority", "Critical", None),  # not one of the four
        ("priority", "Highest", None),
        (
            "opportunityTypes",
            ["find information", "Analyse Data"],
            ["Find Information", "Analyse Data"],
        ),
        ("opportunityTypes", "Predict Issues", ["Predict Issues"]),
        ("opportunityTypes", ["Find Information", "Teleport"], None),  # one bad value rejects all
        ("opportunityTypes", [], None),
        ("opportunityTypes", ["Find Information", "find information"], ["Find Information"]),
    ],
)
def test_values_are_validated_and_never_coerced_into_facts(field, raw, expected):
    assert clean_value(field, raw) == expected


# --- prompts and templates ------------------------------------------------------------------------


def test_the_prompt_version_is_recorded():
    assert PROMPT_VERSION.count(".") == 1 and PROMPT_VERSION[:4] == "2026"


def test_every_field_has_an_ask_and_a_different_follow_up_that_each_pass_the_checker():
    assert set(QUESTIONS) == set(REQUIRED_ORDER) | {"businessImpact", "kpis"}
    for field, (ask, clarify) in QUESTIONS.items():
        assert ask != clarify, field
        assert validate_question(ask, field), (field, ask)
        assert validate_question(clarify, field), (field, clarify)


@pytest.mark.parametrize("field", sorted(CONFIRM))
def test_dedicated_confirmation_questions_are_single_questions(field):
    question = render_question("confirm", field, {field: "Shift logs? and stuff"}, attempts=0)
    assert (
        "Shift logs and stuff" in question
    )  # quoted back, '?' removed so it reads as one question
    assert validate_question(question, field), question


def test_a_conflict_question_asks_which_to_keep_in_one_question():
    q = render_question("clarify", "team", {"team": "Ops"}, attempts=0, conflict_new="Finance")
    assert q.count("?") == 1 and "Ops" in q and "Finance" in q
    assert CONFLICT.count("?") == 1


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("team", "Which team owns this, and what data does it use?"),  # two requests
        ("team", "Which team owns this? And who will use it?"),  # two questions
        ("team", "Which team owns this?; what is the budget?"),
        ("team", "Which team owns this and also who will use it?"),
        ("team", "1. Which team owns this?"),
        ("team", "- Which team owns this?"),
        ("team", "Tell me about the team."),  # not a question
        ("team", "Which team owns this"),  # no question mark
        ("team", "Which team owns this??"),
        ("team", "What data would it need?"),  # a different information need
        ("data", "What data would it need, and which systems hold it?"),  # drifts into systems
        ("currentProcess", "How is this done today and which tools are used?"),
        ("team", "x?" * 200),
        ("team", ""),
    ],
)
def test_compound_or_off_target_questions_are_rejected(field, text):
    assert not validate_question(text, field)


def test_the_six_opportunity_names_are_not_mistaken_for_other_topics():
    ask = QUESTIONS["opportunityTypes"][0]
    assert "Analyse Data" in ask and validate_question(ask, "opportunityTypes")
