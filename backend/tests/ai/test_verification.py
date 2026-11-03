from botocore.exceptions import ClientError

from app.ai.verification import (
    check_inference_destinations,
    check_invocation_logging_disabled,
    check_streaming_supported,
    run_all,
)
from tests.ai.conftest import SELECTION_A, SELECTION_B, aws_error


def arn(region, model="test.model-b:1"):
    return {"modelArn": f"arn:aws:bedrock:{region}::foundation-model/{model}"}


class FakeControlPlane:
    def __init__(self, *, logging=None, profile_regions=(), streaming=True, error=None):
        self._logging, self._regions, self._streaming, self._error = (
            logging,
            profile_regions,
            streaming,
            error,
        )

    def _maybe_raise(self):
        if self._error:
            raise ClientError(aws_error(self._error, status=403), "Op")

    def get_model_invocation_logging_configuration(self):
        self._maybe_raise()
        return {"loggingConfig": self._logging} if self._logging is not None else {}

    def get_inference_profile(self, inferenceProfileIdentifier):
        self._maybe_raise()
        return {"models": [arn(r) for r in self._regions]}

    def get_foundation_model(self, modelIdentifier):
        self._maybe_raise()
        return {"modelDetails": {"responseStreamingSupported": self._streaming}}


def test_logging_disabled_when_unconfigured_or_empty():
    assert check_invocation_logging_disabled(FakeControlPlane()).ok
    assert check_invocation_logging_disabled(FakeControlPlane(logging={})).ok


def test_logging_enabled_is_a_failure():
    cw = {"cloudWatchConfig": {"logGroupName": "g"}, "textDataDeliveryEnabled": True}
    result = check_invocation_logging_disabled(FakeControlPlane(logging=cw))
    assert (result.ok, result.code) == (False, "logging_enabled")
    s3 = {"s3Config": {"bucketName": "b"}}
    assert not check_invocation_logging_disabled(FakeControlPlane(logging=s3)).ok


def test_destinations_must_be_australian_and_match_the_evidence():
    good = FakeControlPlane(profile_regions=["ap-southeast-2", "ap-southeast-4"])
    assert check_inference_destinations(good, SELECTION_B).ok

    overseas = FakeControlPlane(profile_regions=["ap-southeast-2", "us-east-1"])
    assert check_inference_destinations(overseas, SELECTION_B).code == "non_australian_destination"

    drifted = FakeControlPlane(profile_regions=["ap-southeast-2"])  # evidence listed two
    assert (
        check_inference_destinations(drifted, SELECTION_B).code
        == "destinations_differ_from_evidence"
    )


def test_direct_model_has_no_profile_to_check():
    assert (
        check_inference_destinations(FakeControlPlane(), SELECTION_A).code
        == "direct_model_single_region"
    )


def test_streaming_support_checked_per_underlying_model():
    assert check_streaming_supported(FakeControlPlane(streaming=True), SELECTION_A).ok
    assert not check_streaming_supported(FakeControlPlane(streaming=False), SELECTION_A).ok


def test_an_unverifiable_check_fails_rather_than_passes():
    results = run_all(FakeControlPlane(error="AccessDeniedException"), SELECTION_B)
    assert [r.ok for r in results] == [False, False, False]
    assert all(r.code.startswith("check_unavailable:") for r in results)
