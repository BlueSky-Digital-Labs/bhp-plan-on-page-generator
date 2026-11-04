"""Where assistant replies come from.

The interviewer plugs in here. The default producer refuses rather than fabricating output, and
a scripted producer exists for presentation tests and local demos only (never allowed when
deployed).
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class ProducerUnavailable(Exception):
    """No assistant is wired up. Becomes a `failed` stream event, never a made-up reply."""


@dataclass(frozen=True)
class TurnContext:
    """What a producer may know about the turn it is answering. Server-derived, session-scoped."""

    session_id: str
    turn_id: str  # the assistant turn being produced
    user_turn_id: str  # the answer it responds to
    user_text: str
    user_turn_ids: tuple[str, ...]  # every user turn so far, for source-reference checks
    session_expires_at: datetime


class TurnProducer(Protocol):
    def stream(self, ctx: TurnContext) -> Iterator[str]: ...


class UnavailableProducer:
    def stream(self, ctx: TurnContext) -> Iterator[str]:
        raise ProducerUnavailable
        yield ""  # pragma: no cover - makes this a generator


class ScriptedTestProducer:
    """Deterministic, fixed text delivered in small pieces. Does not echo or use the input."""

    REPLY = "Thanks for sharing that. Could you tell me who would use this, and what they do today?"

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self._delay = delay_seconds

    def stream(self, ctx: TurnContext) -> Iterator[str]:
        for word in self.REPLY.split(" "):
            if self._delay:
                time.sleep(self._delay)
            yield word + " "
