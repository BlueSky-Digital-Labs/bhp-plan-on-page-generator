import dataclasses
import logging

import pytest
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from pydantic import BaseModel, ConfigDict

from app.ai import (
    CompletionStatus,
    ConverseRequest,
    ConverseStreamAdapter,
    InvalidRequestError,
    TextDelta,
    ToolCall,
    ToolDefinition,
    ToolInputRejected,
)
from app.ai.policy import ValidatedBedrockConfig
from app.ai.types import FailureCode, ToolRejection
from tests.ai.conftest import (
    SECRET,
    SELECTION_B,
    FakeBedrockRuntime,
    aws_error,
    block_stop,
    client_error,
    message_stop,
    metadata,
    stream_error,
    text,
    tool_events,
    tool_start,
    validated,
)

MESSAGES = [{"role": "user", "content": [{"text": SECRET}]}]


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    count: int


TOOL = ToolDefinition(name="write_field", description="Write a canvas field.", input_model=Canvas)
REQUEST = ConverseRequest(messages=MESSAGES, system=[SECRET], tools=[TOOL])


def run(config, events=(), **client_kwargs):
    client = FakeBedrockRuntime(events, **client_kwargs)
    adapter = ConverseStreamAdapter(client, validated(config))
    return client, adapter


# --- same adapter, different model configurations ----------------------------------


def test_two_model_configs_reach_the_sdk_through_the_same_adapter(config_a, config_b):
    client_a, adapter_a = run(config_a, [text("hi"), message_stop(), metadata()])
    client_b, adapter_b = run(config_b, [text("hi"), message_stop(), metadata()])
    assert type(adapter_a) is type(adapter_b)

    adapter_a.collect(ConverseRequest(messages=MESSAGES, system=["sys"]))
    adapter_b.collect(ConverseRequest(messages=MESSAGES, system=["sys"]))

    call_a, call_b = client_a.calls[0], client_b.calls[0]
    assert call_a["modelId"] == "test.model-a:0"
    assert call_b["modelId"] == "au.test.model-b:1"
    # Model A: standard settings and a provider-specific field are both forwarded.
    assert call_a["inferenceConfig"] == {
        "maxTokens": 512,
        "temperature": 0.2,
        "stopSequences": ["END"],
    }
    assert call_a["additionalModelRequestFields"] == {"top_k": 40}
    # Model B: only what was configured is sent; nothing else, and no provider fields.
    assert call_b["inferenceConfig"] == {"maxTokens": 1024, "topP": 0.9}
    assert "additionalModelRequestFields" not in call_b
    for call in (call_a, call_b):
        assert call["system"] == [{"text": "sys"}]
        assert call["messages"] == MESSAGES
        assert "toolConfig" not in call


def test_changing_identifier_needs_no_adapter_change(config_a):
    swapped = config_a.model_copy(
        update={"model_id": "au.test.model-b:1", "region": "ap-southeast-4"}
    )
    swapped = swapped.model_copy(
        update={
            "inference_config": config_a.inference_config.model_copy(
                update={"temperature": None, "stop_sequences": None}
            ),
            "additional_model_request_fields": {"reasoning_effort": "low"},
        }
    )
    client, adapter = run(swapped, [text("x"), message_stop()])
    adapter.collect(ConverseRequest(messages=MESSAGES))
    assert client.calls[0]["modelId"] == "au.test.model-b:1"
    assert client.calls[0]["additionalModelRequestFields"] == {"reasoning_effort": "low"}


def test_request_has_no_inference_config_when_nothing_configured(config_b):
    # Policy normally requires maxTokens; relax only that, to prove nothing else is invented.
    selection = dataclasses.replace(SELECTION_B, require_max_tokens=False)
    bare = ValidatedBedrockConfig(config_b.model_copy(update={"inference_config": None}), selection)
    kwargs = ConverseStreamAdapter(FakeBedrockRuntime(), bare).build_request(
        ConverseRequest(messages=MESSAGES)
    )
    assert set(kwargs) == {"modelId", "messages"}


# --- streaming text and tools --------------------------------------------------------


def test_streams_text_deltas_and_completes(config_a):
    _, adapter = run(config_a, [text("Hel"), text("lo"), message_stop("end_turn"), metadata()])
    events = list(adapter.stream(ConverseRequest(messages=MESSAGES)))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    end = events[-1]
    assert end.status is CompletionStatus.COMPLETE and end.failure is None
    assert end.stop_reason == "end_turn"
    assert (end.usage.input_tokens, end.usage.output_tokens, end.usage.latency_ms) == (7, 3, 42)


def test_tool_call_is_validated_and_typed(config_a):
    events = [text("ok"), *tool_events("tu-1", "write_field", {"title": "Plan", "count": 3})]
    _, adapter = run(config_a, events)
    result = adapter.collect(REQUEST)
    assert result.committable and result.text == "ok"
    (call,) = result.tool_calls
    assert isinstance(call, ToolCall) and call.tool_use_id == "tu-1" and call.name == "write_field"
    assert call.input == Canvas(title="Plan", count=3)


def test_tool_definition_is_sent_as_toolspec_with_json_schema(config_a):
    client, adapter = run(config_a, [message_stop()])
    adapter.collect(REQUEST)
    spec = client.calls[0]["toolConfig"]["tools"][0]["toolSpec"]
    assert spec["name"] == "write_field"
    assert spec["inputSchema"]["json"]["required"] == ["title", "count"]
    assert "toolChoice" not in client.calls[0]["toolConfig"]


def test_forced_tool_choice_only_when_model_supports_it(config_a, config_b):
    client_b, adapter_b = run(config_b, [message_stop()])
    adapter_b.collect(ConverseRequest(messages=MESSAGES, tools=[TOOL], tool_choice="write_field"))
    assert client_b.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "write_field"}}

    _, adapter_a = run(config_a)
    with pytest.raises(InvalidRequestError):
        adapter_a.build_request(ConverseRequest(messages=MESSAGES, tools=[TOOL], tool_choice="any"))


@pytest.mark.parametrize(
    "request_",
    [
        ConverseRequest(messages=[]),
        ConverseRequest(messages=MESSAGES, tools=[TOOL, TOOL]),
        ConverseRequest(messages=MESSAGES, tool_choice="any"),
    ],
)
def test_unservable_requests_fail_before_any_call(config_b, request_):
    client, adapter = run(config_b)
    with pytest.raises(InvalidRequestError):
        list(adapter.stream(request_))
    assert client.calls == []


# --- malformed / invalid tool output -------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"title": "Plan"}, ToolRejection.SCHEMA_VIOLATION),  # missing field
        ({"title": "Plan", "count": "many"}, ToolRejection.SCHEMA_VIOLATION),
        ({"title": "Plan", "count": 1, "extra": SECRET}, ToolRejection.SCHEMA_VIOLATION),
        (["not", "an", "object"], ToolRejection.SCHEMA_VIOLATION),
    ],
)
def test_invalid_tool_arguments_are_rejected_and_never_authoritative(config_a, payload, reason):
    _, adapter = run(config_a, tool_events("tu-1", "write_field", payload))
    result = adapter.collect(REQUEST)
    assert result.tool_calls == []
    assert result.rejected == [ToolInputRejected("tu-1", reason)]
    assert not result.committable
    assert result.end.failure.code is FailureCode.INVALID_TOOL_INPUT
    assert SECRET not in repr(result.rejected) and SECRET not in repr(result.end)


def test_malformed_json_tool_input_is_rejected(config_a):
    events = [
        tool_start("tu-1", "write_field"),
        {
            "contentBlockDelta": {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": '{"title": "x'}},
            }
        },
        block_stop(),
        message_stop("tool_use"),
    ]
    _, adapter = run(config_a, events)
    result = adapter.collect(REQUEST)
    assert result.rejected == [ToolInputRejected("tu-1", ToolRejection.INVALID_JSON)]
    assert not result.committable


def test_unknown_tool_name_is_rejected(config_a):
    _, adapter = run(config_a, tool_events("tu-1", "delete_everything", {}))
    result = adapter.collect(REQUEST)
    assert result.rejected == [ToolInputRejected("tu-1", ToolRejection.UNKNOWN_TOOL)]
    assert not result.committable


def test_forced_tool_with_no_tool_call_is_not_committable(config_b):
    _, adapter = run(config_b, [text("I will not call it"), message_stop("end_turn")])
    result = adapter.collect(
        ConverseRequest(messages=MESSAGES, tools=[TOOL], tool_choice="write_field")
    )
    assert not result.committable
    assert result.end.failure.code is FailureCode.MISSING_TOOL_CALL


class Other(BaseModel):
    note: str


OTHER_TOOL = ToolDefinition(name="other_tool", description="Another tool.", input_model=Other)


def test_forced_tool_with_rejected_call_is_not_committable_and_reports_why(config_b):
    # The model "calls" the forced tool but with invalid input: nothing valid was produced.
    events = tool_events("tu-1", "write_field", {"title": "Plan"}, stop="end_turn")
    _, adapter = run(config_b, events)
    result = adapter.collect(
        ConverseRequest(messages=MESSAGES, tools=[TOOL], tool_choice="write_field")
    )
    assert result.tool_calls == []
    assert result.rejected == [ToolInputRejected("tu-1", ToolRejection.SCHEMA_VIOLATION)]
    assert not result.committable
    assert result.end.failure.code is FailureCode.INVALID_TOOL_INPUT


def test_named_tool_choice_is_not_satisfied_by_a_different_valid_tool(config_b):
    events = tool_events("tu-1", "other_tool", {"note": "x"})
    _, adapter = run(config_b, events)
    request = ConverseRequest(
        messages=MESSAGES, tools=[TOOL, OTHER_TOOL], tool_choice="write_field"
    )
    result = adapter.collect(request)
    assert [c.name for c in result.tool_calls] == ["other_tool"]  # valid, but not the forced one
    assert not result.committable
    assert result.end.failure.code is FailureCode.MISSING_TOOL_CALL


def test_any_tool_choice_accepts_any_validated_tool(config_b):
    events = tool_events("tu-1", "other_tool", {"note": "x"})
    _, adapter = run(config_b, events)
    request = ConverseRequest(messages=MESSAGES, tools=[TOOL, OTHER_TOOL], tool_choice="any")
    assert adapter.collect(request).committable


# --- interrupted and abnormal streams ------------------------------------------------


def test_stream_that_ends_without_message_stop_is_incomplete(config_a):
    _, adapter = run(config_a, [text("partial output")])
    result = adapter.collect(REQUEST)
    assert result.text == "partial output"
    assert result.end.status is CompletionStatus.INCOMPLETE
    assert result.end.failure.code is FailureCode.STREAM_INTERRUPTED
    assert result.end.failure.partial_output and not result.end.failure.retryable
    assert not result.committable


def test_interruption_inside_a_tool_block_yields_no_tool_call(config_a):
    events = [
        tool_start("tu-1", "write_field"),
        {
            "contentBlockDelta": {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": '{"title"'}},
            }
        },
    ]
    _, adapter = run(config_a, events, raise_mid_stream=stream_error("ModelStreamErrorException"))
    result = adapter.collect(REQUEST)
    assert result.tool_calls == [] and not result.committable
    assert result.end.status is CompletionStatus.INCOMPLETE
    assert result.end.failure.code is FailureCode.STREAM_ERROR


def test_error_after_partial_output_is_incomplete_and_never_replayed(config_a):
    client, adapter = run(
        config_a, [text("part")], raise_mid_stream=stream_error("ThrottlingException")
    )
    result = adapter.collect(REQUEST)
    assert result.end.status is CompletionStatus.INCOMPLETE
    assert result.end.failure.code is FailureCode.THROTTLED
    assert result.end.failure.retryable is False  # partial output: not transparently retryable
    assert len(client.calls) == 1


def test_error_event_inside_the_stream_is_translated(config_a):
    _, adapter = run(
        config_a, [text("a"), {"internalServerException": {"message": SECRET}}, text("b")]
    )
    result = adapter.collect(REQUEST)
    assert result.text == "a"  # nothing is processed after the error
    assert result.end.failure.code is FailureCode.INTERNAL_ERROR
    assert SECRET not in repr(result.end)


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("max_tokens", FailureCode.OUTPUT_TRUNCATED),
        ("malformed_tool_use", FailureCode.MALFORMED_MODEL_OUTPUT),
        ("guardrail_intervened", FailureCode.CONTENT_BLOCKED),
        ("something_new", FailureCode.UNKNOWN_STOP_REASON),
    ],
)
def test_abnormal_stop_reasons_are_not_committable(config_a, reason, code):
    _, adapter = run(config_a, [text("x"), message_stop(reason)])
    result = adapter.collect(REQUEST)
    assert not result.committable and result.end.failure.code is code


@pytest.mark.parametrize("reason", ["end_turn", "stop_sequence", "tool_use"])
def test_normal_stop_reasons_complete(config_a, reason):
    _, adapter = run(config_a, [text("x"), message_stop(reason)])
    assert adapter.collect(ConverseRequest(messages=MESSAGES)).committable


# --- provider failures at call time ---------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (client_error("ThrottlingException", 429), FailureCode.THROTTLED, True),
        (client_error("ModelTimeoutException", 408), FailureCode.TIMEOUT, True),
        (client_error("ServiceUnavailableException", 503), FailureCode.SERVICE_UNAVAILABLE, True),
        (client_error("ValidationException"), FailureCode.INVALID_REQUEST, False),
        (client_error("AccessDeniedException", 403), FailureCode.ACCESS_DENIED, False),
        (client_error("ResourceNotFoundException", 404), FailureCode.MODEL_NOT_FOUND, False),
        (
            ReadTimeoutError(endpoint_url="https://bedrock-runtime.ap-southeast-2.amazonaws.com"),
            FailureCode.TIMEOUT,
            True,
        ),
        (ConnectTimeoutError(endpoint_url="https://x"), FailureCode.TIMEOUT, True),
        (RuntimeError(f"boom {SECRET}"), FailureCode.UNEXPECTED_ERROR, False),
    ],
)
def test_call_failures_become_content_free_failed_results(config_a, exc, code, retryable):
    _, adapter = run(config_a, raise_on_call=exc)
    result = adapter.collect(REQUEST)
    assert result.end.status is CompletionStatus.FAILED
    assert result.end.failure.code is code and result.end.failure.retryable is retryable
    assert SECRET not in repr(result)


def test_failure_carries_request_id_and_status_but_no_message(config_a):
    _, adapter = run(config_a, raise_on_call=client_error("ThrottlingException", 429))
    failure = adapter.collect(REQUEST).end.failure
    assert (failure.request_id, failure.http_status) == ("req-123", 429)
    assert SECRET not in repr(failure)


def test_provider_error_code_is_sanitised(config_a):
    odd = client_error("Weird Code <script>", 400)
    _, adapter = run(config_a, raise_on_call=odd)
    assert adapter.collect(REQUEST).end.failure.exception_type == "unknown"


def test_no_silent_fallback_to_another_model(config_a):
    client, adapter = run(config_a, raise_on_call=client_error("ResourceNotFoundException", 404))
    adapter.collect(REQUEST)
    assert [c["modelId"] for c in client.calls] == ["test.model-a:0"]


# --- no content in logs ----------------------------------------------------------------


def test_logs_contain_metadata_only(config_a):
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("app.ai")
    handler = Collect()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        events = [text(SECRET), *tool_events("tu-1", "write_field", {"title": SECRET})]
        _, adapter = run(config_a, events)
        adapter.collect(REQUEST)
        _, failing = run(config_a, raise_on_call=client_error("ThrottlingException", 429))
        failing.collect(REQUEST)
    finally:
        logger.removeHandler(handler)

    assert len(records) == 2
    dump = " ".join(f"{r.getMessage()} {r.__dict__}" for r in records)
    assert SECRET not in dump
    assert (
        records[0].failure_code == "invalid_tool_input" and records[0].model_id == "test.model-a:0"
    )
    assert records[1].failure_code == "throttled"


def test_aws_error_helper_really_contains_the_secret():
    # Guard: proves the no-leak assertions above are meaningful.
    assert SECRET in aws_error("X")["Error"]["Message"]
