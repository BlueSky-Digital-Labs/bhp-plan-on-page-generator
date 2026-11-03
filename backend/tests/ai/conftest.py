"""Fakes for the Bedrock tests. Model ids here are synthetic and NOT production-approved."""

import datetime as dt
import json
from collections.abc import Iterable

import pytest
from botocore.exceptions import ClientError, EventStreamError

from app.ai.config import BedrockConfig, InferenceConfig
from app.ai.policy import (
    ApprovedSelection,
    Capability,
    Evidence,
    IdentifierKind,
    ValidatedBedrockConfig,
    validate_bedrock_config,
)

SECRET = "SECRET-PLAN-CONTENT-DO-NOT-LEAK"


def _evidence() -> Evidence:
    return Evidence(
        verified_on=dt.date.today(),
        verified_by="test fixture",
        regional="fixture: synthetic destinations",
        privacy="fixture: synthetic",
        capabilities="fixture: synthetic",
        sources=("fixture",),
    )


# Two deliberately different fictional models: different identifier kind, limits,
# sampling rules, tool-choice support and provider-specific fields.
SELECTION_A = ApprovedSelection(
    model_id="test.model-a:0",
    identifier_kind=IdentifierKind.FOUNDATION_MODEL,
    foundation_model_ids=("test.model-a:0",),
    client_regions=("ap-southeast-2",),
    inference_regions=("ap-southeast-2",),
    capabilities=frozenset({Capability.CONVERSE_STREAM, Capability.TOOL_USE}),
    evidence=_evidence(),
    max_tokens_limit=4096,
    temperature_range=(0.0, 1.0),
    top_p_range=(0.0, 1.0),
    max_stop_sequences=4,
    temperature_and_top_p_exclusive=False,
    allowed_additional_fields=frozenset({"top_k"}),
)
SELECTION_B = ApprovedSelection(
    model_id="au.test.model-b:1",
    identifier_kind=IdentifierKind.INFERENCE_PROFILE,
    foundation_model_ids=("test.model-b:1",),
    client_regions=("ap-southeast-2", "ap-southeast-4"),
    inference_regions=("ap-southeast-2", "ap-southeast-4"),
    capabilities=frozenset(
        {Capability.CONVERSE_STREAM, Capability.TOOL_USE, Capability.FORCED_TOOL_CHOICE}
    ),
    evidence=_evidence(),
    max_tokens_limit=8192,
    temperature_range=(0.0, 2.0),
    top_p_range=(0.1, 1.0),
    max_stop_sequences=2,
    temperature_and_top_p_exclusive=True,
    allowed_additional_fields=frozenset({"reasoning_effort"}),
)
REGISTRY = (SELECTION_A, SELECTION_B)


def validated(config: BedrockConfig) -> ValidatedBedrockConfig:
    return validate_bedrock_config(config, REGISTRY)


@pytest.fixture
def config_a() -> BedrockConfig:
    return BedrockConfig(
        region="ap-southeast-2",
        model_id=SELECTION_A.model_id,
        inference_config=InferenceConfig(max_tokens=512, temperature=0.2, stop_sequences=("END",)),
        additional_model_request_fields={"top_k": 40},
    )


@pytest.fixture
def config_b() -> BedrockConfig:
    # Different model, and only maxTokens set: everything else must be omitted.
    return BedrockConfig(
        region="ap-southeast-4",
        model_id=SELECTION_B.model_id,
        inference_config=InferenceConfig(max_tokens=1024, top_p=0.9),
    )


# --- ConverseStream event builders (the SDK's real event shapes) -------------------


def text(delta: str, index: int = 0) -> dict:
    return {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"text": delta}}}


def tool_start(tool_use_id: str, name: str, index: int = 1) -> dict:
    return {
        "contentBlockStart": {
            "contentBlockIndex": index,
            "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}},
        }
    }


def tool_input(fragment: str, index: int = 1) -> dict:
    return {
        "contentBlockDelta": {"contentBlockIndex": index, "delta": {"toolUse": {"input": fragment}}}
    }


def block_stop(index: int = 1) -> dict:
    return {"contentBlockStop": {"contentBlockIndex": index}}


def message_stop(reason: str = "end_turn") -> dict:
    return {"messageStop": {"stopReason": reason}}


def metadata() -> dict:
    return {
        "metadata": {"usage": {"inputTokens": 7, "outputTokens": 3}, "metrics": {"latencyMs": 42}}
    }


def tool_events(tool_use_id: str, name: str, payload: object, stop: str = "tool_use") -> list[dict]:
    raw = json.dumps(payload)
    mid = len(raw) // 2  # fragmented, as the service streams it
    return [
        tool_start(tool_use_id, name),
        tool_input(raw[:mid]),
        tool_input(raw[mid:]),
        block_stop(),
        message_stop(stop),
        metadata(),
    ]


def aws_error(code: str, message: str = SECRET, status: int = 400) -> dict:
    return {
        "Error": {"Code": code, "Message": message},
        "ResponseMetadata": {"HTTPStatusCode": status, "RequestId": "req-123"},
    }


class FakeBedrockRuntime:
    """Records every converse_stream call. Never touches the network."""

    def __init__(
        self,
        events: Iterable[dict] = (),
        *,
        raise_on_call: Exception | None = None,
        raise_mid_stream: Exception | None = None,
    ) -> None:
        self.events = list(events)
        self.raise_on_call = raise_on_call
        self.raise_mid_stream = raise_mid_stream
        self.calls: list[dict] = []

    def converse_stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_on_call:
            raise self.raise_on_call
        return {"stream": self._iterate()}

    def _iterate(self):
        yield from self.events
        if self.raise_mid_stream:
            raise self.raise_mid_stream


def client_error(code: str, status: int = 400) -> ClientError:
    return ClientError(aws_error(code, status=status), "ConverseStream")


def stream_error(code: str) -> EventStreamError:
    return EventStreamError(aws_error(code), "ConverseStream")
