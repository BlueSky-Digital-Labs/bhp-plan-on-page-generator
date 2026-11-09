"""Which part of the canvas does the user mean? (deterministic, from the words they typed)"""

import pytest

from app.interview.amend import PICK_OPTIONS, SHORT_NAMES, is_done, is_negative, mentioned_fields
from app.interview.canvas import ALL_FIELDS, LABELS


def test_every_part_has_a_name_in_the_list_and_nothing_is_missing():
    assert set(SHORT_NAMES) == ALL_FIELDS and len(PICK_OPTIONS) == 16
    assert all("?" not in name for name in PICK_OPTIONS)


@pytest.mark.parametrize("field", sorted(ALL_FIELDS))
def test_each_name_in_the_list_and_each_canvas_label_means_exactly_that_part(field):
    assert mentioned_fields(SHORT_NAMES[field]) == [field]
    assert mentioned_fields(LABELS[field]) == [field]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I want to update the opportunity", ["opportunityTypes"]),
        ("change opportunity to Analyse Data", ["opportunityTypes"]),  # the answer's own words
        ("make the priority Must-have", ["priority"]),
        ("fix the team name", ["team"]),  # "name" belongs to "team name" here
        ("change the name", ["solutionName"]),
        ("the pain points are wrong", ["painPoints"]),
        ("the systems are wrong", ["systems"]),
        ("change the data and systems", ["data", "systems"]),
        ("change the systems and the data", ["systems", "data"]),  # in the order they appear
        ("something is wrong", []),
        ("", []),
    ],
)
def test_the_parts_named_in_a_message(text, expected):
    assert mentioned_fields(text) == expected


def test_a_word_inside_another_word_is_not_a_part():
    assert mentioned_fields("the database is fine, the userland too") == []


@pytest.mark.parametrize(
    "text", ["thanks", "Thank you!", "ok", "looks good", "that's all", "nothing else"]
)
def test_short_thanks_are_not_requests_to_change(text):
    assert is_done(text)


@pytest.mark.parametrize(
    "text",
    [
        "change the team",
        "I want to update the opportunity",
        "ok but fix the team please, it is wrong",
    ],
)
def test_anything_else_is_not_a_thank_you(text):
    assert not is_done(text)


@pytest.mark.parametrize("text", ["no", "No, I meant the team", "never mind", "cancel", "thanks"])
def test_a_no_or_a_thank_you_backs_out(text):
    assert is_negative(text)


def test_a_new_answer_is_not_a_no():
    assert not is_negative("Operations")
    assert not is_negative("Nothing is wrong with the current process, it just takes long")
