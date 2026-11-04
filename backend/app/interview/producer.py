import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from app.conversation.producers import TurnContext
from app.interview.engine import Interviewer, Outcome
from app.interview.store import InMemoryInterviewStore

_MAX_IN_FLIGHT = 1000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InterviewerProducer:
    """Plugs the interviewer into the conversation stream.

    `stream` works out the reply without changing anything. `commit` runs once, only after the
    assistant turn has been stored, so a dropped connection leaves the draft exactly as it was.
    """

    def __init__(
        self,
        interviewer: Interviewer,
        store: InMemoryInterviewStore,
        *,
        delay_seconds: float = 0.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._interviewer, self._store, self._delay, self._clock = (
            interviewer,
            store,
            delay_seconds,
            clock,
        )
        self._outcomes: dict[str, Outcome] = {}

    def stream(self, ctx: TurnContext) -> Iterator[str]:
        state = self._store.get(ctx.session_id)
        outcome = self._interviewer.process(
            state, ctx.user_text, ctx.user_turn_id, ctx.user_turn_ids
        )
        if len(self._outcomes) >= _MAX_IN_FLIGHT:  # abandoned streams cannot pile up
            self._outcomes.clear()
        self._outcomes[ctx.turn_id] = outcome
        for i, word in enumerate(outcome.text.split(" ")):
            if self._delay:
                time.sleep(self._delay)
            yield (" " if i else "") + word

    def commit(self, ctx: TurnContext) -> None:
        outcome = self._outcomes.pop(ctx.turn_id, None)
        if outcome is not None:
            self._store.put(ctx.session_id, outcome.state, ctx.session_expires_at, self._clock())
