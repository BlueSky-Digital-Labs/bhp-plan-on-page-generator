import pytest

from app.ai.config import (
    BedrockConfig,
    BedrockSelectionError,
    InferenceConfig,
    load_bedrock_config,
)

DEFAULT = BedrockConfig(
    region="ap-southeast-2",
    model_id="code.default:0",
    inference_config=InferenceConfig(max_tokens=256, temperature=0.5),
)


def test_shipped_default_is_no_selection(monkeypatch):
    # No model has been verified, so nothing is configured and loading must fail safely.
    with pytest.raises(BedrockSelectionError) as excinfo:
        load_bedrock_config()
    assert excinfo.value.codes == ("no_selection",)
    assert excinfo.value.settings == ("model_id",)


def test_code_default_used_when_no_overrides():
    assert load_bedrock_config(DEFAULT) == DEFAULT


def test_env_overrides_code_default_field_by_field(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env.model:9")
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "1000")
    config = load_bedrock_config(DEFAULT)
    assert config.model_id == "env.model:9"
    assert config.inference_config.max_tokens == 1000
    # Not overridden: stays as in code.
    assert config.region == "ap-southeast-2"
    assert config.inference_config.temperature == 0.5


def test_env_alone_can_define_the_whole_selection(monkeypatch):
    monkeypatch.setenv("BEDROCK_REGION", "ap-southeast-4")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env.only:1")
    monkeypatch.setenv("BEDROCK_TOP_P", "0.8")
    monkeypatch.setenv("BEDROCK_STOP_SEQUENCES", '["</done>"]')
    monkeypatch.setenv("BEDROCK_ADDITIONAL_MODEL_REQUEST_FIELDS", '{"top_k": 20}')
    config = load_bedrock_config(None)
    assert config.to_converse_fields() == {
        "modelId": "env.only:1",
        "inferenceConfig": {"topP": 0.8, "stopSequences": ["</done>"]},
        "additionalModelRequestFields": {"top_k": 20},
    }


def test_invalid_env_value_names_variable_not_value(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "x:0")
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "not-a-number-SECRET")
    with pytest.raises(BedrockSelectionError) as excinfo:
        load_bedrock_config(DEFAULT)
    assert "invalid_setting" in excinfo.value.codes
    assert "BEDROCK_MAX_TOKENS" in str(excinfo.value)
    assert "SECRET" not in str(excinfo.value)


def test_out_of_range_value_is_rejected_by_type_validation(monkeypatch):
    monkeypatch.setenv("BEDROCK_TOP_P", "7")
    with pytest.raises(BedrockSelectionError) as excinfo:
        load_bedrock_config(DEFAULT)
    assert "inference_config.top_p" in excinfo.value.settings


@pytest.mark.parametrize(
    ("env", "value"),
    [
        ("BEDROCK_MAX_TOKENS", "0"),
        ("BEDROCK_TEMPERATURE", "-1"),
        ("BEDROCK_TEMPERATURE", "nan"),
        ("BEDROCK_MAX_ATTEMPTS", "99"),
        ("BEDROCK_READ_TIMEOUT_SECONDS", "0"),
        ("BEDROCK_ADDITIONAL_MODEL_REQUEST_FIELDS", "{}"),
        ("BEDROCK_STOP_SEQUENCES", '[""]'),
    ],
)
def test_invalid_settings_rejected(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises(BedrockSelectionError):
        load_bedrock_config(DEFAULT)


def test_wrong_types_in_code_are_rejected():
    with pytest.raises(ValueError):
        InferenceConfig(max_tokens=True)
    with pytest.raises(ValueError):
        InferenceConfig(max_tokens="100")
    with pytest.raises(ValueError):
        BedrockConfig(
            region="ap-southeast-2", model_id="m", additional_model_request_fields={"a": {1, 2}}
        )


def test_unset_settings_are_omitted_from_sdk_fields():
    bare = BedrockConfig(region="ap-southeast-2", model_id="m:0")
    assert bare.to_converse_fields() == {"modelId": "m:0"}
    only_tokens = BedrockConfig(
        region="ap-southeast-2", model_id="m:0", inference_config=InferenceConfig(max_tokens=5)
    )
    assert only_tokens.to_converse_fields()["inferenceConfig"] == {"maxTokens": 5}


def test_provider_fields_are_separate_and_copied():
    extra = {"nested": {"k": 1}}
    config = BedrockConfig(
        region="ap-southeast-2", model_id="m:0", additional_model_request_fields=extra
    )
    fields = config.to_converse_fields()
    assert "additionalModelRequestFields" in fields and "inferenceConfig" not in fields
    fields["additionalModelRequestFields"]["nested"]["k"] = 2
    assert config.additional_model_request_fields["nested"]["k"] == 1
