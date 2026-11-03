"""Bedrock model selection and generation settings.

Where to change things
----------------------
* Developers: edit `DEFAULT_BEDROCK_CONFIG` below (the central code configuration).
* Operators: set `BEDROCK_*` environment variables on the deployment. These override
  the code default field by field. Precedence, highest first:

      1. BEDROCK_* environment variables   (only the ones that are set)
      2. DEFAULT_BEDROCK_CONFIG            (this file)
      3. nothing -> no selection -> `BedrockSelectionError(no_selection)`

Neither source is enough on its own to *use* a model: the effective configuration must
also pass the approved-model policy in `app.ai.policy`. These are developer/operator
settings. No HTTP route, request field or user input can reach them.

Callers and the adapter never name a model. They receive a validated configuration.
"""

import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ConfigError, failed_fields

StrictFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
StrictInt = Annotated[int, Field(strict=True)]


class InferenceConfig(BaseModel):
    """Standard Converse inference settings. Unset (None) settings are never sent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_tokens: Annotated[StrictInt, Field(ge=1)] | None = None
    temperature: Annotated[StrictFloat, Field(ge=0)] | None = None
    top_p: Annotated[StrictFloat, Field(gt=0, le=1)] | None = None
    stop_sequences: tuple[Annotated[str, Field(min_length=1)], ...] | None = None

    def to_sdk(self) -> dict[str, Any]:
        """Serialise to the SDK's `inferenceConfig`, omitting anything unset."""
        fields = {
            "maxTokens": self.max_tokens,
            "temperature": self.temperature,
            "topP": self.top_p,
            "stopSequences": list(self.stop_sequences) if self.stop_sequences else None,
        }
        return {key: value for key, value in fields.items() if value is not None}


class BedrockConfig(BaseModel):
    """Everything the adapter needs to call ConverseStream. Never holds credentials."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    region: Annotated[str, Field(min_length=1)]
    # A foundation-model id or an inference-profile id/ARN. Not interpreted by the adapter.
    model_id: Annotated[str, Field(min_length=1)]
    inference_config: InferenceConfig | None = None
    # Provider-specific request fields. Kept apart from `inference_config` on purpose.
    additional_model_request_fields: dict[str, Any] | None = None

    # Client behaviour. max_attempts is the total number of tries (including the first) and
    # covers only establishing the request; a stream that has begun
    # is never replayed.
    connect_timeout_seconds: Annotated[StrictFloat, Field(gt=0, le=60)] = 5.0
    read_timeout_seconds: Annotated[StrictFloat, Field(gt=0, le=300)] = 60.0
    max_attempts: Annotated[StrictInt, Field(ge=1, le=5)] = 3

    @field_validator("additional_model_request_fields")
    @classmethod
    def _json_serialisable(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not value or any(not isinstance(key, str) or not key for key in value):
            raise ValueError("must be a non-empty object with non-empty string keys")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("must be JSON-serialisable") from None
        return value

    def to_converse_fields(self) -> dict[str, Any]:
        """The model-related ConverseStream arguments: modelId and optional config."""
        fields: dict[str, Any] = {"modelId": self.model_id}
        if self.inference_config is not None:
            inference = self.inference_config.to_sdk()
            if inference:
                fields["inferenceConfig"] = inference
        if self.additional_model_request_fields is not None:
            fields["additionalModelRequestFields"] = json.loads(
                json.dumps(self.additional_model_request_fields)
            )
        return fields


# ---------------------------------------------------------------------------------
# Central code configuration. Edit here to change the model or its generation settings.
#
# Deliberately None: no model has been verified and approved yet (see docs/bedrock.md).
# Replace with, for example (after completing the approval checklist in the docs):
#
#   DEFAULT_BEDROCK_CONFIG = BedrockConfig(
#       region="ap-southeast-2",
#       model_id="<verified model or inference-profile id>",
#       inference_config=InferenceConfig(max_tokens=2048, temperature=0.3),
#   )
# ---------------------------------------------------------------------------------
DEFAULT_BEDROCK_CONFIG: BedrockConfig | None = None


class BedrockOverrides(BaseSettings):
    """Deployment overrides, read through the same pydantic-settings loader as `Settings`.

    Every field is optional; only fields that are actually set override the code default.
    List/object values are JSON, e.g. BEDROCK_STOP_SEQUENCES='["</answer>"]'.
    """

    model_config = SettingsConfigDict(env_prefix="BEDROCK_", extra="ignore")

    region: Annotated[str, Field(min_length=1)] | None = None
    model_id: Annotated[str, Field(min_length=1)] | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] | None = None
    additional_model_request_fields: dict[str, Any] | None = None
    connect_timeout_seconds: float | None = None
    read_timeout_seconds: float | None = None
    max_attempts: int | None = None


_INFERENCE_FIELDS = ("max_tokens", "temperature", "top_p", "stop_sequences")


def _env_names(exc: ValidationError) -> str:
    return ", ".join(f"BEDROCK_{name.upper()}" for name in failed_fields(exc).split(", "))


class BedrockSelectionError(ConfigError):
    """The Bedrock selection is missing, invalid or not approved.

    `codes` are stable machine-readable reasons; `settings` name the offending settings.
    The message never contains configured values, prompts, credentials or provider text.
    """

    def __init__(self, codes: list[str], settings: list[str] | None = None) -> None:
        self.codes = tuple(dict.fromkeys(codes))
        self.settings = tuple(dict.fromkeys(settings or ()))
        detail = f" (settings: {', '.join(self.settings)})" if self.settings else ""
        super().__init__(f"Bedrock configuration rejected: {', '.join(self.codes)}{detail}")


def load_bedrock_config(default: BedrockConfig | None = DEFAULT_BEDROCK_CONFIG) -> BedrockConfig:
    """Merge `BEDROCK_*` overrides onto the code default and validate types and ranges.

    Raises BedrockSelectionError(no_selection) if neither source names a model, and
    (invalid_setting) naming the environment variables that failed validation.
    """
    try:
        overrides = BedrockOverrides()
    except ValidationError as exc:
        raise BedrockSelectionError(["invalid_setting"], _env_names(exc).split(", ")) from None

    set_values = overrides.model_dump(exclude_unset=True, exclude_none=True)
    merged: dict[str, Any] = default.model_dump() if default is not None else {}
    inference: dict[str, Any] = dict(merged.pop("inference_config", None) or {})

    for name, value in set_values.items():
        if name in _INFERENCE_FIELDS:
            inference[name] = tuple(value) if name == "stop_sequences" else value
        else:
            merged[name] = value
    inference = {k: v for k, v in inference.items() if v is not None}
    if inference:
        merged["inference_config"] = inference

    if not merged.get("model_id"):
        raise BedrockSelectionError(["no_selection"], ["model_id"])
    if not merged.get("region"):
        raise BedrockSelectionError(["invalid_setting"], ["region"])

    try:
        return BedrockConfig.model_validate(merged)
    except ValidationError as exc:
        raise BedrockSelectionError(["invalid_setting"], failed_fields(exc).split(", ")) from None
