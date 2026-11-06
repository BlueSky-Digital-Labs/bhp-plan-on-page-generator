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
    TIPS,
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
    assert PRIORITIES == ("Must-have", "Nice-to-have", "Exploratory")


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
        ("priority", "must have", "Must-have"),
        ("priority", "MUST-HAVE", "Must-have"),
        ("priority", "nice to have", "Nice-to-have"),
        ("priority", "Nice-to-have", "Nice-to-have"),
        ("priority", "exploratory", "Exploratory"),
        ("priority", "Critical", None),  # not one of the four
        ("priority", "High", None),
        ("priority", "Strategic", None),
        ("priority", "Musthave", None),
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


def test_the_opportunity_question_lists_every_option_so_the_user_can_see_what_to_choose_from():
    ask, follow_up = QUESTIONS["opportunityTypes"]
    for text in (ask, follow_up):
        assert all(name in text for name in OPPORTUNITY_TYPES), text
        assert validate_question(text, "opportunityTypes")
    assert "more than one" in TIPS["opportunityTypes"]  # said right after the first ask


def test_a_follow_up_shows_how_to_answer_with_an_example():
    for field, (ask, follow_up) in QUESTIONS.items():
        if field in ("priority", "businessImpact", "kpis"):  # choices and optional prompts
            continue
        assert "like “" in follow_up, field  # a concrete way to answer
        assert "like “" not in ask, field  # the first ask stays direct


def test_the_priority_follow_up_asks_why_in_one_question():
    question = render_question("probe", "priority", {"priority": "Must-have"}, attempts=0)
    assert question == "Why did you give this idea the priority “Must-have”?"
    assert validate_question(question, "priority")


def test_every_field_has_a_specific_first_ask_and_a_how_to_answer_hint():
    for field, (ask, clarify) in QUESTIONS.items():
        assert field in TIPS and len(TIPS[field]) > 20, field  # a hint shown after the first ask
        assert ask != clarify and len(ask.split()) >= 7, field  # specific, not a bare label


def test_open_ended_asks_lead_with_concrete_things_to_think_about():
    leads = {
        "painPoints": "delays, mistakes",
        "data": "logs, reports",
        "systems": "rostering",
        "risks": "approvals",
        "businessImpact": "time saved, cost, safety",
        "kpis": "hours saved",
        "currentProcess": "first step to the last",
    }
    for field, words in leads.items():
        assert words in QUESTIONS[field][0], field


def test_the_priority_question_names_the_three_options_and_the_hint_explains_them():
    ask, clarify = QUESTIONS["priority"]
    for text in (ask, clarify):
        assert "Must-have, Nice-to-have or Exploratory" in text
        assert not any(old in text for old in ("Low", "Medium", "High", "Strategic"))
    tip = TIPS["priority"]
    assert all(word in tip for word in ("Must-have", "Nice-to-have", "Exploratory"))
