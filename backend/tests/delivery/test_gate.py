"""The gate: both documents for the current revision, and nothing else, unlock deletion."""

from datetime import UTC, datetime

import pytest

from app.delivery import Delivery, evaluate_gate

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def held(**revisions):
    return {k: Delivery(k, r, NOW) for k, r in revisions.items()}


def gate(revision=3, *, started=True, complete=True, finished=True, **revisions):
    return evaluate_gate(
        revision,
        started=started,
        complete=complete,
        reply_finished=finished,
        deliveries=held(**revisions),
    )


def test_both_documents_for_the_current_revision_unlock_deletion():
    result = gate(canvas=3, appendix=3)
    assert result.purge_eligible and result.lock_reason is None
    assert result.canvas_downloaded and result.appendix_downloaded and result.revision == 3


@pytest.mark.parametrize(
    ("revisions", "canvas", "appendix", "reason"),
    [
        ({}, False, False, "both_not_downloaded"),
        ({"canvas": 3}, True, False, "appendix_not_downloaded"),
        ({"appendix": 3}, False, True, "canvas_not_downloaded"),
    ],
)
def test_each_missing_document_is_named(revisions, canvas, appendix, reason):
    result = gate(**revisions)
    assert (result.canvas_downloaded, result.appendix_downloaded) == (canvas, appendix)
    assert not result.purge_eligible and result.lock_reason == reason


def test_an_amendment_resets_both_documents_at_once():
    result = gate(revision=4, canvas=3, appendix=3)  # answered again since
    assert not result.canvas_downloaded and not result.appendix_downloaded
    assert not result.purge_eligible and result.lock_reason == "both_not_downloaded"


def test_documents_from_two_different_revisions_never_count_together():
    result = gate(revision=4, canvas=3, appendix=4)
    assert result.appendix_downloaded and not result.canvas_downloaded
    assert result.lock_reason == "canvas_not_downloaded" and not result.purge_eligible


def test_a_record_from_a_later_revision_does_not_count_for_this_one():
    result = gate(revision=3, canvas=4, appendix=4)
    assert not result.canvas_downloaded and not result.purge_eligible


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"started": False}, "not_ready"),
        ({"finished": False}, "turn_in_progress"),
    ],
)
def test_nothing_unlocks_with_nothing_to_save_or_a_reply_still_coming(kwargs, reason):
    result = gate(canvas=3, appendix=3, **kwargs)
    assert not result.purge_eligible and result.lock_reason == reason


def test_an_unfinished_draft_unlocks_the_same_way_and_says_it_is_a_draft():
    draft = gate(complete=False, canvas=3, appendix=3)
    assert draft.purge_eligible and draft.lock_reason is None and draft.complete is False
    assert gate(canvas=3, appendix=3).complete is True
    # still needs both documents, for the current revision
    assert not gate(complete=False, canvas=3).purge_eligible
    assert not gate(complete=False, revision=4, canvas=3, appendix=3).purge_eligible


def test_the_view_has_exactly_the_documented_fields():
    assert set(gate(canvas=3).view()) == {
        "revision",
        "canvas_downloaded",
        "appendix_downloaded",
        "purge_eligible",
        "lock_reason",
        "complete",
    }
