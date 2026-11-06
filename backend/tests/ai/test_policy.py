import datetime as dt
import json

import pytest

from app.ai.config import BedrockConfig, BedrockSelectionError, InferenceConfig
from app.ai.policy import (
    APPROVED_SELECTIONS,
    ApprovedSelection,
    Capability,
    Evidence,
    LocalApprovalsError,
    load_local_approvals,
    validate_bedrock_config,
)
from app.config import AppEnv
from tests.ai.conftest import REGISTRY, SELECTION_A, SELECTION_B, VERIFIED_ON

AU = "ap-southeast-2"


def cfg(model_id=SELECTION_A.model_id, region=AU, extra=None, **inference):
    return BedrockConfig(
        region=region,
        model_id=model_id,
        inference_config=InferenceConfig(**inference) if inference else None,
        additional_model_request_fields=extra,
    )


def codes(config, registry=REGISTRY):
    with pytest.raises(BedrockSelectionError) as excinfo:
        validate_bedrock_config(config, registry)
    return excinfo.value


def test_production_registry_holds_only_the_two_test_only_entries():
    """A tripwire: adding or changing an approval has to be a deliberate change to this test.

    Both entries are for the shared test account and say so. Nothing here is release approval.
    """
    assert [s.model_id for s in APPROVED_SELECTIONS] == [
        "au.anthropic.claude-sonnet-5-5",
        "openai.gpt-oss-120b-1:0",
    ]
    for selection in APPROVED_SELECTIONS:
        assert "not release evidence" in selection.evidence.regional.casefold()
        assert "not verified" in selection.evidence.privacy.casefold()
        assert set(selection.inference_regions) <= {"ap-southeast-2", "ap-southeast-4"}
    assert codes(cfg(), None).codes == ("unapproved_model",)  # any other model is still refused


def test_the_sonnet_profile_can_be_selected_but_does_not_claim_forced_tool_choice():
    config = BedrockConfig(
        region="ap-southeast-2",
        model_id="au.anthropic.claude-sonnet-5-5",
        inference_config=InferenceConfig(max_tokens=1024),
    )
    chosen = validate_bedrock_config(config).selection
    assert chosen.model_id == "au.anthropic.claude-sonnet-5-5"
    assert Capability.TOOL_USE in chosen.capabilities
    assert Capability.FORCED_TOOL_CHOICE not in chosen.capabilities


def test_the_gpt_oss_model_can_be_selected_but_does_not_claim_forced_tool_choice():
    config = BedrockConfig(
        region="ap-southeast-2",
        model_id="openai.gpt-oss-120b-1:0",
        inference_config=InferenceConfig(max_tokens=1024),
    )
    chosen = validate_bedrock_config(config).selection
    assert Capability.FORCED_TOOL_CHOICE not in chosen.capabilities


def test_a_token_limit_above_the_approved_ceiling_is_refused_for_both_test_entries():
    for model in ("au.anthropic.claude-sonnet-5-5", "openai.gpt-oss-120b-1:0"):
        config = BedrockConfig(
            region="ap-southeast-2",
            model_id=model,
            inference_config=InferenceConfig(max_tokens=8193),
        )
        assert codes(config, None).codes == ("unsupported_setting",)


def test_unapproved_identifier_rejected_without_substitution():
    error = codes(cfg(model_id="some.other-model:0", max_tokens=10))
    assert error.codes == ("unapproved_model",)


def test_valid_configs_pass_for_both_models(config_a, config_b):
    assert validate_bedrock_config(config_a, REGISTRY).selection is SELECTION_A
    assert validate_bedrock_config(config_b, REGISTRY).selection is SELECTION_B


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1", "ap-southeast-1"])
def test_non_australian_client_region_rejected(region):
    error = codes(cfg(region=region, max_tokens=10))
    assert "region_not_approved" in error.codes and "region" in error.settings


def test_australian_region_not_in_approval_rejected():
    # ap-southeast-4 is Australian but SELECTION_A was only approved for Sydney.
    assert "region_not_approved" in codes(cfg(region="ap-southeast-4", max_tokens=10)).codes


def test_max_tokens_required_by_default():
    error = codes(cfg())
    assert error.codes == ("missing_setting",)
    assert error.settings == ("inference_config.max_tokens",)


@pytest.mark.parametrize(
    ("kwargs", "setting"),
    [
        ({"max_tokens": 5000}, "inference_config.max_tokens"),
        (
            {"max_tokens": 10, "stop_sequences": ("1", "2", "3", "4", "5")},
            "inference_config.stop_sequences",
        ),
    ],
)
def test_out_of_model_range_rejected(kwargs, setting):
    assert setting in codes(cfg(**kwargs)).settings


def test_model_specific_ranges_differ():
    # temperature 1.5 is fine for model B, not for model A.
    assert "inference_config.temperature" in codes(cfg(max_tokens=10, temperature=1.5)).settings
    validate_bedrock_config(cfg(SELECTION_B.model_id, max_tokens=10, temperature=1.5), REGISTRY)


def test_incompatible_combination_rejected():
    error = codes(cfg(SELECTION_B.model_id, max_tokens=10, temperature=0.5, top_p=0.5))
    assert error.codes == ("incompatible_settings",)


def test_unsupported_provider_field_rejected_not_dropped():
    error = codes(cfg(max_tokens=10, extra={"reasoning_effort": "high"}))
    assert error.settings == ("additional_model_request_fields",)
    validate_bedrock_config(cfg(max_tokens=10, extra={"top_k": 1}), REGISTRY)


def test_all_problems_reported_together_without_values():
    error = codes(
        cfg(SELECTION_B.model_id, region="us-east-1", max_tokens=99999, temperature=0.5, top_p=0.5)
    )
    assert {"region_not_approved", "unsupported_setting", "incompatible_settings"} <= set(
        error.codes
    )
    assert "99999" not in str(error)


def _entry(**overrides):
    base = {
        "model_id": "m:0",
        "identifier_kind": SELECTION_A.identifier_kind,
        "foundation_model_ids": ("m:0",),
        "client_regions": (AU,),
        "inference_regions": (AU,),
        "capabilities": frozenset({Capability.CONVERSE_STREAM, Capability.TOOL_USE}),
        "evidence": SELECTION_A.evidence,
        "max_tokens_limit": 10,
        "temperature_range": (0.0, 1.0),
        "top_p_range": (0.0, 1.0),
        "max_stop_sequences": 1,
        "temperature_and_top_p_exclusive": False,
        "allowed_additional_fields": frozenset(),
    }
    return ApprovedSelection(**(base | overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"inference_regions": (AU, "us-east-1")},  # overseas destination
        {"inference_regions": ("ap-southeast-1",)},  # Singapore is not Australian
        {"client_regions": ("us-west-2",)},
        {"capabilities": frozenset({Capability.CONVERSE_STREAM})},  # no tool use
        {"capabilities": frozenset({Capability.TOOL_USE})},  # no streaming
    ],
)
def test_an_unsafe_approval_entry_cannot_exist(overrides):
    with pytest.raises(ValueError, match="invalid approved selection"):
        _entry(**overrides)


def test_evidence_must_be_complete_and_not_future_dated():
    ok = dict(verified_on=VERIFIED_ON, verified_by="a", regional="r", privacy="p", capabilities="c")
    with pytest.raises(ValueError):
        Evidence(**ok, sources=())
    with pytest.raises(ValueError):
        Evidence(**(ok | {"privacy": " "}), sources=("s",))
    with pytest.raises(ValueError):
        Evidence(**(ok | {"verified_on": dt.date.today() + dt.timedelta(days=1)}), sources=("s",))


# --- local-only approvals file ------------------------------------------------------


def _local_entry(**overrides):
    entry = {
        "model_id": "local.test-model:0",
        "identifier_kind": "foundation_model",
        "foundation_model_ids": ["local.test-model:0"],
        "client_regions": [AU],
        "inference_regions": [AU],
        "capabilities": ["converse_stream", "tool_use"],
        "evidence": {
            "verified_on": "2026-01-01",
            "verified_by": "local tester",
            "regional": "local functional test only",
            "privacy": "not verified",
            "capabilities": "smoke test",
            "sources": ["local"],
        },
        "max_tokens_limit": 4096,
        "temperature_range": [0.0, 1.0],
        "top_p_range": [0.0, 1.0],
        "max_stop_sequences": 4,
        "temperature_and_top_p_exclusive": False,
        "allowed_additional_fields": [],
    }
    return {**entry, **overrides}


def _write(tmp_path, entries):
    path = tmp_path / "approvals.local.json"
    path.write_text(json.dumps(entries))
    return path


def test_local_approvals_make_a_model_usable_locally_only(tmp_path):
    path = _write(tmp_path, [_local_entry()])
    (selection,) = load_local_approvals(AppEnv.LOCAL, path)
    assert selection.model_id == "local.test-model:0"
    config = cfg(model_id="local.test-model:0", max_tokens=100)
    assert validate_bedrock_config(config, (*APPROVED_SELECTIONS, selection)).selection is selection
    # The production list is untouched, so without the file the model is still rejected.
    assert codes(config, APPROVED_SELECTIONS).codes == ("unapproved_model",)


@pytest.mark.parametrize("app_env", [AppEnv.TEST, AppEnv.DEPLOYED])
def test_local_approvals_file_is_ignored_outside_local(tmp_path, app_env):
    assert load_local_approvals(app_env, _write(tmp_path, [_local_entry()])) == ()


def test_missing_local_approvals_file_means_no_extra_approvals(tmp_path):
    assert load_local_approvals(AppEnv.LOCAL, tmp_path / "absent.json") == ()


@pytest.mark.parametrize(
    "entries",
    [
        [_local_entry(client_regions=["us-east-1"])],  # same Australian-only rule as policy.py
        [_local_entry(evidence={})],  # incomplete evidence
        [{"model_id": "x"}],  # missing fields
        {"not": "a list"},
    ],
)
def test_invalid_local_approvals_are_rejected_without_echoing_content(tmp_path, entries):
    path = _write(tmp_path, entries)
    with pytest.raises(LocalApprovalsError) as excinfo:
        load_local_approvals(AppEnv.LOCAL, path)
    assert "us-east-1" not in str(excinfo.value) and "local tester" not in str(excinfo.value)
