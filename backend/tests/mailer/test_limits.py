from datetime import UTC, datetime, timedelta

import pytest

from app.mailer.limits import SendLimiter, SendRefused

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
END = NOW + timedelta(hours=24)


def limiter(**kwargs):
    clock = {"now": NOW}
    made = SendLimiter(clock=lambda: clock["now"], **kwargs)
    return made, clock


def send(made, who="s1", end=END, *, sent=True):
    made.begin(who, end)
    made.finish(who, end, sent=sent)


def refused(made, who="s1", end=END) -> str:
    with pytest.raises(SendRefused) as caught:
        made.begin(who, end)
    return caught.value.code


def test_a_session_may_send_up_to_its_limit_and_no_more():
    made, _ = limiter(per_session=2)
    send(made)
    send(made)
    assert refused(made) == "limit"
    send(made, "s2")  # another session is not affected


def test_a_send_that_did_not_go_through_does_not_use_up_the_allowance():
    made, _ = limiter(per_session=1)
    for _ in range(5):
        send(made, sent=False)
    send(made)
    assert refused(made) == "limit"


def test_only_one_send_runs_at_a_time_in_a_session():
    made, _ = limiter()
    made.begin("s1", END)
    assert refused(made) == "in_progress"
    made.begin("s2", END)  # a different session can send meanwhile
    made.finish("s1", END, sent=False)
    made.begin("s1", END)


def test_the_hold_is_released_even_when_nothing_was_sent():
    made, _ = limiter()
    made.begin("s1", END)
    made.finish("s1", END, sent=False)
    send(made)


def test_entries_end_with_their_session_and_a_new_session_starts_fresh():
    made, clock = limiter(per_session=1)
    send(made)
    assert refused(made) == "limit"
    clock["now"] = END
    send(made)  # the old entry is gone, so the same id counts as new


def test_a_full_table_refuses_new_sessions_instead_of_forgetting_live_ones():
    made, _ = limiter(per_session=1, capacity=2)
    send(made, "a")
    send(made, "b")
    assert refused(made, "c") == "full"
    assert refused(made, "a") == "limit"  # existing sessions keep their count
    made.forget("a")
    send(made, "c")


def test_forgetting_a_session_clears_its_count_and_any_hold():
    made, _ = limiter(per_session=1)
    made.begin("s1", END)
    made.forget("s1")
    send(made)
    made.forget("s1")
    send(made)
