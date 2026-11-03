"""Live checks against the AWS control plane (bedrock, not bedrock-runtime).

Run these with a verification/admin role when approving or re-validating a selection,
and periodically afterwards. The workload role does NOT need these permissions
(bedrock:GetInferenceProfile, bedrock:GetFoundationModel,
bedrock:GetModelInvocationLoggingConfiguration). A check that cannot run reports
`ok=False`: an unverifiable requirement is a blocker, never a pass.
"""

from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from app.ai.policy import AUSTRALIAN_REGIONS, ApprovedSelection, IdentifierKind


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    code: str  # stable, content-free reason


def _unavailable(name: str, exc: Exception) -> CheckResult:
    if isinstance(exc, ClientError):
        return CheckResult(name, False, f"check_unavailable:{exc.response['Error']['Code']}")
    return CheckResult(name, False, f"check_unavailable:{type(exc).__name__}")


def check_invocation_logging_disabled(bedrock: Any) -> CheckResult:
    """Model invocation logging must stay off: it would store prompts and completions."""
    name = "invocation_logging_disabled"
    try:
        logging_config = bedrock.get_model_invocation_logging_configuration().get("loggingConfig")
    except (ClientError, BotoCoreError) as exc:
        return _unavailable(name, exc)
    destinations = (logging_config or {}).get("cloudWatchConfig") or (logging_config or {}).get(
        "s3Config"
    )
    if destinations:
        return CheckResult(name, False, "logging_enabled")
    return CheckResult(name, True, "disabled")


def check_inference_destinations(bedrock: Any, selection: ApprovedSelection) -> CheckResult:
    """Every region the profile can route to must be Australian and match the evidence."""
    name = "inference_destinations_australian"
    if selection.identifier_kind is not IdentifierKind.INFERENCE_PROFILE:
        return CheckResult(name, True, "direct_model_single_region")
    try:
        profile = bedrock.get_inference_profile(inferenceProfileIdentifier=selection.model_id)
    except (ClientError, BotoCoreError) as exc:
        return _unavailable(name, exc)
    # arn:aws:bedrock:<region>::foundation-model/<id>
    regions = {m["modelArn"].split(":")[3] for m in profile.get("models", [])}
    if not regions or not regions <= AUSTRALIAN_REGIONS:
        return CheckResult(name, False, "non_australian_destination")
    if regions != set(selection.inference_regions):
        return CheckResult(name, False, "destinations_differ_from_evidence")
    return CheckResult(name, True, "matches_evidence")


def check_streaming_supported(bedrock: Any, selection: ApprovedSelection) -> CheckResult:
    """The control plane reports streaming support; tool support is evidenced from docs."""
    name = "streaming_supported"
    try:
        for model_id in selection.foundation_model_ids:
            details = bedrock.get_foundation_model(modelIdentifier=model_id)["modelDetails"]
            if not details.get("responseStreamingSupported"):
                return CheckResult(name, False, "streaming_not_supported")
    except (ClientError, BotoCoreError) as exc:
        return _unavailable(name, exc)
    return CheckResult(name, True, "supported")


def run_all(bedrock: Any, selection: ApprovedSelection) -> list[CheckResult]:
    return [
        check_invocation_logging_disabled(bedrock),
        check_inference_destinations(bedrock, selection),
        check_streaming_supported(bedrock, selection),
    ]
