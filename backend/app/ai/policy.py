"""Approved-model policy: what may actually be invoked, with dated evidence.

A configured identifier is only *usable* if an `ApprovedSelection` names it. An entry
exists only after someone has verified, for that exact identifier, the Australian-only
inference destinations, the provider data-handling position, the ConverseStream and
tool capabilities, and the IAM resources (procedure: docs/bedrock.md). Changing the
model therefore needs a new or updated entry here, not just a new identifier.

`APPROVED_SELECTIONS` holds only the two TEST-ONLY entries for the shared test account (marked
"NOT release evidence" in their evidence): no model has release approval yet, and a model must never
be added from memory.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.ai.config import BedrockConfig, BedrockSelectionError
from app.config import AppEnv

logger = logging.getLogger("app.ai")

# Australian AWS Regions. Every client region and every inference destination must be here.
AUSTRALIAN_REGIONS = frozenset({"ap-southeast-2", "ap-southeast-4"})


class Capability(StrEnum):
    CONVERSE_STREAM = "converse_stream"
    TOOL_USE = "tool_use"
    FORCED_TOOL_CHOICE = "forced_tool_choice"


class IdentifierKind(StrEnum):
    FOUNDATION_MODEL = "foundation_model"
    INFERENCE_PROFILE = "inference_profile"


@dataclass(frozen=True)
class Evidence:
    """Dated, sourced verification. Every field is required."""

    verified_on: dt.date
    verified_by: str
    regional: str  # how Australian-only processing was established, incl. every destination
    privacy: str  # provider prompt storage / training position and where it is documented
    capabilities: str  # how ConverseStream + tool support were established
    sources: tuple[str, ...]  # authoritative URLs or console/CLI outputs

    def __post_init__(self) -> None:
        texts = (self.verified_by, self.regional, self.privacy, self.capabilities)
        if not all(t.strip() for t in texts) or not self.sources or not all(self.sources):
            raise ValueError("evidence must be complete")
        if self.verified_on > dt.date.today():
            raise ValueError("evidence date is in the future")


@dataclass(frozen=True)
class ApprovedSelection:
    model_id: str
    identifier_kind: IdentifierKind
    # Underlying foundation models, for IAM resources behind a profile.
    foundation_model_ids: tuple[str, ...]
    client_regions: tuple[str, ...]
    # EVERY region the request can be processed in (for a direct model id: the client region).
    inference_regions: tuple[str, ...]
    capabilities: frozenset[Capability]
    evidence: Evidence
    # Model-specific limits for the generation settings.
    max_tokens_limit: int
    temperature_range: tuple[float, float]
    top_p_range: tuple[float, float]
    max_stop_sequences: int  # 0 = stop sequences unsupported
    temperature_and_top_p_exclusive: bool
    allowed_additional_fields: frozenset[str]  # top-level keys only
    require_max_tokens: bool = True  # unset maxTokens reserves the model maximum of quota

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not self.model_id or not self.foundation_model_ids:
            problems.append("identifiers")
        if not self.client_regions or not set(self.client_regions) <= AUSTRALIAN_REGIONS:
            problems.append("client_regions")
        if not self.inference_regions or not set(self.inference_regions) <= AUSTRALIAN_REGIONS:
            problems.append("inference_regions")
        if not {Capability.CONVERSE_STREAM, Capability.TOOL_USE} <= self.capabilities:
            problems.append("capabilities")
        if self.max_tokens_limit < 1 or self.max_stop_sequences < 0:
            problems.append("limits")
        for low, high in (self.temperature_range, self.top_p_range):
            if not (math.isfinite(low) and math.isfinite(high) and 0 <= low <= high):
                problems.append("ranges")
        if problems:
            raise ValueError(f"invalid approved selection: {', '.join(dict.fromkeys(problems))}")


# See docs/bedrock.md for the checklist and the evidence register.
#
# TEST-ONLY: these two entries let the deployed app use the models checked in the shared test
# account on 2026-11-05. They are NOT release evidence: privacy is not verified, and tool use was
# not checked against the documentation (the first run is the proof). Before any release, replace
# them with entries that completed the whole checklist, and update the register in docs/bedrock.md.
_SHARED_ACCOUNT_TEST = dt.date(2026, 11, 5)
APPROVED_SELECTIONS: tuple[ApprovedSelection, ...] = (
    ApprovedSelection(
        model_id="au.anthropic.claude-sonnet-5-5",
        identifier_kind=IdentifierKind.INFERENCE_PROFILE,
        foundation_model_ids=("anthropic.claude-sonnet-5-5",),
        client_regions=("ap-southeast-2",),
        inference_regions=("ap-southeast-2", "ap-southeast-4"),
        capabilities=frozenset({Capability.CONVERSE_STREAM, Capability.TOOL_USE}),
        evidence=Evidence(
            verified_on=_SHARED_ACCOUNT_TEST,
            verified_by="local tester (shared test account)",
            regional=(
                "get-inference-profile for au.anthropic.claude-sonnet-5-5 lists foundation-model "
                "ARNs in ap-southeast-2 and ap-southeast-4 only. SHARED-ACCOUNT FUNCTIONAL TEST "
                "ONLY, NOT release evidence."
            ),
            privacy=(
                "NOT VERIFIED for release: the Bedrock data-protection statement is not yet "
                "recorded against this model."
            ),
            capabilities=(
                "Streaming confirmed (get-foundation-model responseStreamingSupported=true). Tool "
                "use NOT verified from the Converse features table: asserted for a functional "
                "test and proven only by running the interview or python -m app.ai.smoke. Forced "
                "tool choice is not claimed: the first real call with it was refused as "
                "invalid_request, so the model is left to choose the tool. Limits are "
                "deliberately conservative, not from the docs."
            ),
            sources=(
                "aws bedrock get-inference-profile (shared test account, 2026-11-05)",
                "aws bedrock get-foundation-model anthropic.claude-sonnet-5-5 (2026-11-05)",
            ),
        ),
        max_tokens_limit=8192,
        temperature_range=(0.0, 1.0),
        top_p_range=(0.0, 1.0),
        max_stop_sequences=4,
        temperature_and_top_p_exclusive=True,
        allowed_additional_fields=frozenset(),
    ),
    ApprovedSelection(
        model_id="openai.gpt-oss-120b-1:0",
        identifier_kind=IdentifierKind.FOUNDATION_MODEL,
        foundation_model_ids=("openai.gpt-oss-120b-1:0",),
        client_regions=("ap-southeast-2",),
        inference_regions=("ap-southeast-2",),
        capabilities=frozenset({Capability.CONVERSE_STREAM, Capability.TOOL_USE}),
        evidence=Evidence(
            verified_on=_SHARED_ACCOUNT_TEST,
            verified_by="local tester (shared test account)",
            regional=(
                "Direct foundation-model id (not an inference profile), listed ON_DEMAND in "
                "ap-southeast-2 by list-foundation-models; a direct id is processed in the client "
                "region. SHARED-ACCOUNT FUNCTIONAL TEST ONLY, NOT release evidence."
            ),
            privacy=(
                "NOT VERIFIED for release: the Bedrock data-protection statement and any "
                "OpenAI-specific terms are not yet recorded against this model."
            ),
            capabilities=(
                "Streaming confirmed (list-foundation-models responseStreamingSupported=true). "
                "Tool use NOT verified from the Converse features table: asserted for a "
                "functional test and proven only by running the interview or python -m "
                "app.ai.smoke. Forced tool choice is not claimed. Limits are conservative."
            ),
            sources=("aws bedrock list-foundation-models --by-provider openai (2026-11-05)",),
        ),
        max_tokens_limit=8192,
        temperature_range=(0.0, 1.0),
        top_p_range=(0.0, 1.0),
        max_stop_sequences=4,
        temperature_and_top_p_exclusive=False,
        allowed_additional_fields=frozenset(),
    ),
)


@dataclass(frozen=True)
class ValidatedBedrockConfig:
    """A configuration that passed the policy. This is what the adapter accepts."""

    config: BedrockConfig
    selection: ApprovedSelection

    def supports(self, capability: Capability) -> bool:
        return capability in self.selection.capabilities


# Untracked, local-testing-only approvals. NOT release evidence: see load_local_approvals.
LOCAL_APPROVALS_FILE = Path(__file__).resolve().parents[2] / "approvals.local.json"


class LocalApprovalsError(ValueError):
    """The local approvals file is unusable. Names the file, never echoes its content."""


def _local_selection(raw: dict[str, Any]) -> ApprovedSelection:
    ev = raw["evidence"]
    return ApprovedSelection(
        model_id=raw["model_id"],
        identifier_kind=IdentifierKind(raw["identifier_kind"]),
        foundation_model_ids=tuple(raw["foundation_model_ids"]),
        client_regions=tuple(raw["client_regions"]),
        inference_regions=tuple(raw["inference_regions"]),
        capabilities=frozenset(Capability(c) for c in raw["capabilities"]),
        evidence=Evidence(
            verified_on=dt.date.fromisoformat(ev["verified_on"]),
            verified_by=ev["verified_by"],
            regional=ev["regional"],
            privacy=ev["privacy"],
            capabilities=ev["capabilities"],
            sources=tuple(ev["sources"]),
        ),
        max_tokens_limit=raw["max_tokens_limit"],
        temperature_range=tuple(raw["temperature_range"]),
        top_p_range=tuple(raw["top_p_range"]),
        max_stop_sequences=raw["max_stop_sequences"],
        temperature_and_top_p_exclusive=raw["temperature_and_top_p_exclusive"],
        allowed_additional_fields=frozenset(raw["allowed_additional_fields"]),
        require_max_tokens=raw.get("require_max_tokens", True),
    )


def load_local_approvals(
    app_env: AppEnv | None, path: Path = LOCAL_APPROVALS_FILE
) -> tuple[ApprovedSelection, ...]:
    """Extra approvals from the gitignored `backend/approvals.local.json`, for local testing.

    Entries get the same validation as `APPROVED_SELECTIONS` (Australian regions, complete
    evidence) but are not release evidence. Read only when `APP_ENV=local`, so a deployed or
    test environment never honours the file and `APPROVED_SELECTIONS` stays the only
    production list. A missing file means no extra approvals.
    """
    if app_env is not AppEnv.LOCAL or not path.is_file():
        return ()
    try:
        entries = json.loads(path.read_text())
        if not isinstance(entries, list):
            raise TypeError
        selections = tuple(_local_selection(entry) for entry in entries)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LocalApprovalsError(
            f"{path.name} is invalid ({type(exc).__name__}); fix or delete it"
        ) from None
    if selections:
        # Metadata only: a reminder that these are not production approvals.
        logger.warning(
            "local_approvals_loaded", extra={"model_ids": [s.model_id for s in selections]}
        )
    return selections


def validate_bedrock_config(
    config: BedrockConfig, approved: Sequence[ApprovedSelection] | None = None
) -> ValidatedBedrockConfig:
    """Check the effective configuration against the approved policy.

    Raises BedrockSelectionError listing every problem found, as codes plus setting
    names. There is no fallback: an unapproved or incompatible selection is rejected,
    never replaced by another model or silently adjusted.
    """
    registry = APPROVED_SELECTIONS if approved is None else tuple(approved)
    selection = next((s for s in registry if s.model_id == config.model_id), None)
    if selection is None:
        raise BedrockSelectionError(["unapproved_model"], ["model_id"])

    codes: list[str] = []
    settings: list[str] = []

    def reject(code: str, setting: str) -> None:
        codes.append(code)
        settings.append(setting)

    if config.region not in AUSTRALIAN_REGIONS or config.region not in selection.client_regions:
        reject("region_not_approved", "region")

    inference = config.inference_config
    max_tokens = inference.max_tokens if inference else None
    temperature = inference.temperature if inference else None
    top_p = inference.top_p if inference else None
    stops = inference.stop_sequences if inference else None

    if max_tokens is None and selection.require_max_tokens:
        reject("missing_setting", "inference_config.max_tokens")
    if max_tokens is not None and max_tokens > selection.max_tokens_limit:
        reject("unsupported_setting", "inference_config.max_tokens")
    if temperature is not None and not (
        selection.temperature_range[0] <= temperature <= selection.temperature_range[1]
    ):
        reject("unsupported_setting", "inference_config.temperature")
    if top_p is not None and not (selection.top_p_range[0] <= top_p <= selection.top_p_range[1]):
        reject("unsupported_setting", "inference_config.top_p")
    if stops is not None and len(stops) > selection.max_stop_sequences:
        reject("unsupported_setting", "inference_config.stop_sequences")
    if selection.temperature_and_top_p_exclusive and temperature is not None and top_p is not None:
        reject("incompatible_settings", "inference_config.temperature, inference_config.top_p")

    extra: dict[str, Any] | None = config.additional_model_request_fields
    if extra is not None and not set(extra) <= selection.allowed_additional_fields:
        reject("unsupported_setting", "additional_model_request_fields")

    if codes:
        raise BedrockSelectionError(codes, settings)
    return ValidatedBedrockConfig(config=config, selection=selection)


__all__ = [
    "APPROVED_SELECTIONS",
    "LocalApprovalsError",
    "AUSTRALIAN_REGIONS",
    "ApprovedSelection",
    "Capability",
    "Evidence",
    "IdentifierKind",
    "ValidatedBedrockConfig",
    "load_local_approvals",
    "validate_bedrock_config",
]
