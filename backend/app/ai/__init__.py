"""Provider-agnostic Bedrock ConverseStream integration. See docs/bedrock.md."""

from app.ai.adapter import ConverseStreamAdapter, InvalidRequestError
from app.ai.client import build_bedrock_runtime_client
from app.ai.config import (
    BedrockConfig,
    BedrockSelectionError,
    InferenceConfig,
    load_bedrock_config,
)
from app.ai.policy import ValidatedBedrockConfig, validate_bedrock_config
from app.ai.types import (
    CompletionStatus,
    ConverseRequest,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolDefinition,
    ToolInputRejected,
    TurnResult,
)


def get_validated_bedrock_config() -> ValidatedBedrockConfig:
    """Effective configuration (env over code default) that passed the approval policy.

    Raises BedrockSelectionError if no approved selection is configured. Called by the
    features that use Bedrock, not at application startup, so the foundation app still
    runs before a model has been approved.
    """
    return validate_bedrock_config(load_bedrock_config())


__all__ = [
    "BedrockConfig",
    "BedrockSelectionError",
    "CompletionStatus",
    "ConverseRequest",
    "ConverseStreamAdapter",
    "InferenceConfig",
    "InvalidRequestError",
    "StreamEnd",
    "TextDelta",
    "ToolCall",
    "ToolDefinition",
    "ToolInputRejected",
    "TurnResult",
    "ValidatedBedrockConfig",
    "build_bedrock_runtime_client",
    "get_validated_bedrock_config",
    "load_bedrock_config",
    "validate_bedrock_config",
]
