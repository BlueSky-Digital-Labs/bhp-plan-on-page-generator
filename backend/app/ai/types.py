"""Application-facing contract of the ConverseStream adapter.

Requests use the SDK's own Converse message shape. Results are typed and content-free
except for the model output the caller asked for (text deltas, validated tool input).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel


class Message(TypedDict):
    """A Converse message, e.g. {"role": "user", "content": [{"text": "..."}]}."""

    role: Literal["user", "assistant"]
    content: list[dict[str, Any]]


_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolDefinition:
    """A tool the model may call. `input_model` is both the JSON schema sent to the model
    and the validator applied to whatever comes back; use `extra="forbid"` on it for a
    strict contract."""

    name: str
    description: str
    input_model: type[BaseModel]

    def __post_init__(self) -> None:
        if not _TOOL_NAME.match(self.name):
            raise ValueError("invalid tool name")

    def to_tool_spec(self) -> dict[str, Any]:
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {"json": self.input_model.model_json_schema()},
            }
        }


@dataclass(frozen=True)
class ConverseRequest:
    messages: Sequence[Message]
    system: Sequence[str] = ()
    tools: Sequence[ToolDefinition] = ()
    # None = model decides. "any" = must call some tool. Otherwise the name of a tool.
    tool_choice: str | None = None


class FailureCode(StrEnum):
    THROTTLED = "throttled"
    TIMEOUT = "timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_ERROR = "internal_error"
    INVALID_REQUEST = "invalid_request"
    ACCESS_DENIED = "access_denied"
    MODEL_NOT_FOUND = "model_not_found"
    NO_CREDENTIALS = "no_credentials"
    CONNECTION_ERROR = "connection_error"
    STREAM_ERROR = "stream_error"
    STREAM_INTERRUPTED = "stream_interrupted"
    OUTPUT_TRUNCATED = "output_truncated"
    MALFORMED_MODEL_OUTPUT = "malformed_model_output"
    CONTENT_BLOCKED = "content_blocked"
    INVALID_TOOL_INPUT = "invalid_tool_input"
    MISSING_TOOL_CALL = "missing_tool_call"
    UNKNOWN_STOP_REASON = "unknown_stop_reason"
    UNEXPECTED_ERROR = "unexpected_error"


RETRYABLE = frozenset(
    {
        FailureCode.THROTTLED,
        FailureCode.TIMEOUT,
        FailureCode.SERVICE_UNAVAILABLE,
        FailureCode.INTERNAL_ERROR,
        FailureCode.CONNECTION_ERROR,
    }
)


@dataclass(frozen=True)
class Failure:
    """Content-free failure metadata. Never holds provider messages, prompts or output."""

    code: FailureCode
    partial_output: bool = False
    exception_type: str | None = None
    http_status: int | None = None
    request_id: str | None = None  # AWS request id, for support; not content

    @property
    def retryable(self) -> bool:
        # A partially delivered response is never safe to retry transparently.
        return self.code in RETRYABLE and not self.partial_output


class CompletionStatus(StrEnum):
    COMPLETE = "complete"  # the only status a consumer may commit
    INCOMPLETE = "incomplete"  # some output arrived but the turn did not finish cleanly
    FAILED = "failed"  # no output arrived


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    """A tool call whose input passed validation against its `input_model`."""

    tool_use_id: str
    name: str
    input: BaseModel


class ToolRejection(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_JSON = "invalid_json"
    SCHEMA_VIOLATION = "schema_violation"


@dataclass(frozen=True)
class ToolInputRejected:
    """A tool call that must not be used. Carries no part of the rejected input."""

    tool_use_id: str
    reason: ToolRejection


@dataclass(frozen=True)
class StreamEnd:
    """Always the last event, exactly once."""

    status: CompletionStatus
    stop_reason: str | None = None
    usage: Usage | None = None
    failure: Failure | None = None


StreamEvent = TextDelta | ToolCall | ToolInputRejected | StreamEnd


@dataclass
class TurnResult:
    """A collected turn. Commit only when `committable`."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    rejected: list[ToolInputRejected] = field(default_factory=list)
    end: StreamEnd | None = None

    @property
    def committable(self) -> bool:
        return self.end is not None and self.end.status is CompletionStatus.COMPLETE
