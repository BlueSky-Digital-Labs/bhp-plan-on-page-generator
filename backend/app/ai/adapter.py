"""Reusable ConverseStream adapter.

Model, region and generation settings come only from the injected, policy-validated
configuration. Nothing here (or in any caller) names a model or provider family.

The adapter is synchronous because boto3 is. Under FastAPI, consume `stream()` from a
sync route/generator (run in the threadpool) or wrap it with `run_in_threadpool`.

Contract
--------
`stream()` yields `TextDelta`, `ToolCall` (input already validated against the tool's
pydantic model) and `ToolInputRejected` events, then exactly one `StreamEnd`. Provider
failures never raise: they become `StreamEnd(status=FAILED|INCOMPLETE, failure=...)`.
Only `COMPLETE` may be committed. Programmer errors (a request the configured model
cannot honour) raise `InvalidRequestError` before any call is made.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator, Sequence
from typing import Any

from pydantic import ValidationError

from app.ai.failures import STREAM_ERROR_EVENTS, failure_from_aws_code, failure_from_exception
from app.ai.policy import Capability, ValidatedBedrockConfig
from app.ai.types import (
    CompletionStatus,
    ConverseRequest,
    Failure,
    FailureCode,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolDefinition,
    ToolInputRejected,
    ToolRejection,
    TurnResult,
    Usage,
)

logger = logging.getLogger("app.ai")

# stopReason values that end a turn normally.
_COMPLETE_REASONS = frozenset({"end_turn", "tool_use", "stop_sequence"})
_INCOMPLETE_REASONS = {
    "max_tokens": FailureCode.OUTPUT_TRUNCATED,
    "model_context_window_exceeded": FailureCode.OUTPUT_TRUNCATED,
    "malformed_model_output": FailureCode.MALFORMED_MODEL_OUTPUT,
    "malformed_tool_use": FailureCode.MALFORMED_MODEL_OUTPUT,
    "guardrail_intervened": FailureCode.CONTENT_BLOCKED,
    "content_filtered": FailureCode.CONTENT_BLOCKED,
}


class InvalidRequestError(ValueError):
    """The request cannot be honoured by the configured model. Safe to log: no content."""


_IDENT = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_PATH_PART = re.compile(r"^[A-Za-z_]{1,32}$")


def _safe_identifier(value: str) -> str:
    """A model-chosen name, only if it looks like an identifier (it could be anything)."""
    return value if _IDENT.match(value) else "?"


def _safe_detail(exc: ValidationError) -> tuple[str, ...]:
    """Which schema paths failed and how ("field_updates.0.field:enum"), never the values.

    Path parts that are not plain schema-style names (a model-invented key) become "?".
    """
    parts: list[str] = []
    for error in exc.errors()[:5]:
        path = ".".join(
            str(p) if isinstance(p, int) or _PATH_PART.match(str(p)) else "?"
            for p in error["loc"][:4]
        )
        parts.append(f"{path}:{error['type']}")
    return tuple(parts)


class _ToolBuffer:
    __slots__ = ("chunks", "name", "tool_use_id")

    def __init__(self, tool_use_id: str, name: str) -> None:
        self.tool_use_id, self.name, self.chunks = tool_use_id, name, []


class _Turn:
    """Per-call stream state machine over the SDK's ConverseStream events."""

    def __init__(self, tools: Sequence[ToolDefinition]) -> None:
        self._tools = {t.name: t for t in tools}
        self._open: dict[int, _ToolBuffer] = {}
        self.emitted = False  # any model output has been delivered
        self.stop_reason: str | None = None
        self.usage: Usage | None = None
        self.failure: Failure | None = None
        self.rejected = False
        self.called: set[str] = set()  # names of tools with a validated call

    def handle(self, event: dict[str, Any]) -> Iterator[StreamEvent]:
        for key, aws_code in STREAM_ERROR_EVENTS.items():
            if key in event:
                self.failure = failure_from_aws_code(aws_code, partial_output=self.emitted)
                return
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                tool = start["toolUse"]
                index = event["contentBlockStart"].get("contentBlockIndex", 0)
                self._open[index] = _ToolBuffer(
                    str(tool.get("toolUseId", "")), str(tool.get("name", ""))
                )
                self.emitted = True
        elif "contentBlockDelta" in event:
            block = event["contentBlockDelta"]
            delta = block.get("delta", {})
            if "text" in delta:
                self.emitted = True
                yield TextDelta(text=delta["text"])
            elif "toolUse" in delta:
                buffer = self._open.get(block.get("contentBlockIndex", 0))
                if buffer is not None:
                    buffer.chunks.append(str(delta["toolUse"].get("input", "")))
            # Other delta kinds (e.g. reasoning) are not part of the application contract.
        elif "contentBlockStop" in event:
            buffer = self._open.pop(event["contentBlockStop"].get("contentBlockIndex", 0), None)
            if buffer is not None:
                yield self._finish_tool(buffer)
        elif "messageStop" in event:
            self.stop_reason = event["messageStop"].get("stopReason")
        elif "metadata" in event:
            meta = event["metadata"]
            usage = meta.get("usage", {})
            self.usage = Usage(
                input_tokens=usage.get("inputTokens"),
                output_tokens=usage.get("outputTokens"),
                latency_ms=meta.get("metrics", {}).get("latencyMs"),
            )

    def _finish_tool(self, buffer: _ToolBuffer) -> ToolCall | ToolInputRejected:
        tool = self._tools.get(buffer.name)
        reason: ToolRejection | None = None
        detail: tuple[str, ...] = ()
        parsed: Any = None
        if tool is None:
            reason = ToolRejection.UNKNOWN_TOOL
            detail = (_safe_identifier(buffer.name),)
        else:
            raw = "".join(buffer.chunks) or "{}"
            try:
                parsed = json.loads(raw)
            except ValueError:
                reason = ToolRejection.INVALID_JSON
        if reason is None and tool is not None:
            try:
                if not isinstance(parsed, dict):
                    raise TypeError
                validated = tool.input_model.model_validate(parsed)
            except (ValidationError, TypeError) as exc:
                reason = ToolRejection.SCHEMA_VIOLATION
                detail = (
                    _safe_detail(exc) if isinstance(exc, ValidationError) else ("not_an_object",)
                )
            else:
                self.called.add(buffer.name)
                return ToolCall(tool_use_id=buffer.tool_use_id, name=buffer.name, input=validated)
        self.rejected = True
        # Reason code only: the rejected input is never retained or logged.
        return ToolInputRejected(
            tool_use_id=buffer.tool_use_id,
            reason=reason or ToolRejection.INVALID_JSON,
            detail=detail,
        )

    def _tool_choice_satisfied(self, tool_choice: str | None) -> bool:
        """A forced choice needs a *validated* call: any tool for "any", else that tool."""
        if tool_choice is None:
            return True
        return bool(self.called) if tool_choice == "any" else tool_choice in self.called

    def end(self, *, tool_choice: str | None) -> StreamEnd:
        failure = self.failure
        if failure is None:
            if self.stop_reason is None or self._open:
                failure = Failure(FailureCode.STREAM_INTERRUPTED, partial_output=self.emitted)
            elif self.rejected:
                failure = Failure(FailureCode.INVALID_TOOL_INPUT, partial_output=True)
            elif self.stop_reason in _INCOMPLETE_REASONS:
                failure = Failure(_INCOMPLETE_REASONS[self.stop_reason], partial_output=True)
            elif self.stop_reason not in _COMPLETE_REASONS:
                failure = Failure(FailureCode.UNKNOWN_STOP_REASON, partial_output=True)
            elif not self._tool_choice_satisfied(tool_choice):
                failure = Failure(FailureCode.MISSING_TOOL_CALL, partial_output=self.emitted)
        if failure is None:
            return StreamEnd(CompletionStatus.COMPLETE, self.stop_reason, self.usage)
        status = CompletionStatus.INCOMPLETE if self.emitted else CompletionStatus.FAILED
        return StreamEnd(status, self.stop_reason, self.usage, failure)


class ConverseStreamAdapter:
    def __init__(
        self,
        client: Any,
        config: ValidatedBedrockConfig,
    ) -> None:
        """`client` is a bedrock-runtime client (or a stub with `converse_stream`)."""
        self._client = client
        self._config = config

    def build_request(self, request: ConverseRequest) -> dict[str, Any]:
        """Serialise to ConverseStream keyword arguments. Unset settings are omitted."""
        if not request.messages:
            raise InvalidRequestError("messages must not be empty")
        names = [t.name for t in request.tools]
        if len(set(names)) != len(names):
            raise InvalidRequestError("tool names must be unique")
        if request.tools and not self._config.supports(Capability.TOOL_USE):
            raise InvalidRequestError("configured model does not support tools")

        kwargs: dict[str, Any] = {
            **self._config.config.to_converse_fields(),
            "messages": list(request.messages),
        }
        if request.system:
            kwargs["system"] = [{"text": text} for text in request.system]
        if request.tools:
            tool_config: dict[str, Any] = {"tools": [t.to_tool_spec() for t in request.tools]}
            if request.tool_choice is not None:
                if not self._config.supports(Capability.FORCED_TOOL_CHOICE):
                    raise InvalidRequestError("configured model does not support tool_choice")
                if request.tool_choice == "any":
                    tool_config["toolChoice"] = {"any": {}}
                elif request.tool_choice in names:
                    tool_config["toolChoice"] = {"tool": {"name": request.tool_choice}}
                else:
                    raise InvalidRequestError("tool_choice names an unknown tool")
            kwargs["toolConfig"] = tool_config
        elif request.tool_choice is not None:
            raise InvalidRequestError("tool_choice requires tools")
        return kwargs

    def stream(self, request: ConverseRequest) -> Iterator[StreamEvent]:
        kwargs = self.build_request(request)
        turn = _Turn(request.tools)
        started = time.monotonic()
        try:
            response = self._client.converse_stream(**kwargs)
            for event in response["stream"]:
                yield from turn.handle(event)
                if turn.failure is not None:
                    break
        except Exception as exc:  # noqa: BLE001 - every SDK failure becomes a typed result
            turn.failure = failure_from_exception(exc, partial_output=turn.emitted)
        end = turn.end(tool_choice=request.tool_choice)
        self._log(end, started, kwargs)
        yield end

    def collect(self, request: ConverseRequest) -> TurnResult:
        result = TurnResult()
        parts: list[str] = []
        for event in self.stream(request):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, ToolCall):
                result.tool_calls.append(event)
            elif isinstance(event, ToolInputRejected):
                result.rejected.append(event)
            else:
                result.end = event
        result.text = "".join(parts)
        return result

    def _log(self, end: StreamEnd, started: float, kwargs: dict[str, Any]) -> None:
        # Metadata only. No messages, system text, completions, tool input or provider text,
        # except the opt-in, short error text (failures.py).
        extra: dict[str, Any] = {
            "model_id": self._config.config.model_id,
            "stop_reason": end.stop_reason,
            "failure_code": end.failure.code.value if end.failure else None,
            "status": end.status.value,
            "input_tokens": end.usage.input_tokens if end.usage else None,
            "output_tokens": end.usage.output_tokens if end.usage else None,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        if end.failure:
            extra["exception_type"] = end.failure.exception_type
            extra["http_status"] = end.failure.http_status
            extra["request_id"] = end.failure.request_id
            extra["request_shape"] = _request_shape(kwargs)
            if end.failure.error_text:
                extra["error_text"] = end.failure.error_text
        logger.info("converse_stream_finished", extra=extra)


def _request_shape(kwargs: dict[str, Any]) -> str:
    """Which parts a request had and how many, never their content. Tool names are constants."""
    tool_config = kwargs.get("toolConfig", {})
    tools = ",".join(t["toolSpec"]["name"] for t in tool_config.get("tools", [])) or "none"
    choice = next(iter(tool_config.get("toolChoice", {})), "none")
    inference = ",".join(sorted(kwargs.get("inferenceConfig", {}))) or "none"
    return (
        f"messages={len(kwargs.get('messages', []))} system={len(kwargs.get('system', []))} "
        f"tools={tools} tool_choice={choice} inference={inference} "
        f"extra_fields={'yes' if 'additionalModelRequestFields' in kwargs else 'no'}"
    )
