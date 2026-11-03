"""Where assistant replies come from.

The real interviewer (feature 11) plugs in here. Until then the default producer refuses
rather than fabricating output, and a scripted producer exists for presentation tests and
local demos only (never allowed when deployed).
"""

import time
from collections.abc import Iterator
from typing import Protocol


class ProducerUnavailable(Exception):
    """No assistant is wired up. Becomes a `failed` stream event, never a made-up reply."""


class TurnProducer(Protocol):
    def stream(self, user_text: str) -> Iterator[str]: ...


class UnavailableProducer:
    def stream(self, user_text: str) -> Iterator[str]:
        raise ProducerUnavailable
        yield ""  # pragma: no cover - makes this a generator


class ScriptedTestProducer:
    """Deterministic, fixed text delivered in small pieces. Does not echo or use the input."""

    REPLY = "Thanks for sharing that. Could you tell me who would use this, and what they do today?"

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self._delay = delay_seconds

    def stream(self, user_text: str) -> Iterator[str]:
        for word in self.REPLY.split(" "):
            if self._delay:
                time.sleep(self._delay)
            yield word + " "
