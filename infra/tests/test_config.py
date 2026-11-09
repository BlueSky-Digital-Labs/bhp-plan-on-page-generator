import re
from pathlib import Path

import pytest

import config
from config import Config, ConfigError, OidcConfig
from tests.conftest import BASE, BEDROCK_ARNS, BEDROCK_ROLE, CERT


def load(**overrides) -> Config:
    ctx = {**BASE, **overrides}
    return Config.from_context(lambda key: ctx.get(key))


def test_defaults():
    cfg = load()
    assert cfg.region == "ap-southeast-2"
    assert cfg.availability_zones == ("ap-southeast-2a", "ap-southeast-2b")
    assert cfg.web_desired_count == 2
    assert cfg.monthly_budget_usd == 450
    assert cfg.rate_limit_sensitive == 100
    assert (cfg.app_name, cfg.header_text, cfg.footer_text) == (
        "SCP AI Ideation assistant",
        "SCP AI Ideation assistant",
        "BHP",
    )
    assert cfg.enable_email is False
    assert cfg.conversation_producer == "unavailable"  # no model approved yet


def test_empty_values_count_as_unset():
    cfg = load(domain_name="", hosted_zone_id="", alarm_email="", app_name="", web_desired_count="")
    assert cfg.domain_name is None and cfg.alarm_email is None
    assert cfg.app_name == "SCP AI Ideation assistant" and cfg.web_desired_count == 2


def test_model_selects_the_bedrock_producer():
    cfg = load(bedrock_model_id="au.example-model-v1:0", bedrock_resource_arns=BEDROCK_ARNS)
    assert cfg.conversation_producer == "interviewer_bedrock"
    assert len(cfg.bedrock_resource_arns) == 3


def test_a_bedrock_role_replaces_the_resource_arns():
    cfg = load(profile="minimal", bedrock_model_id="m", bedrock_role_arn=BEDROCK_ROLE)
    assert cfg.bedrock_role_arn == BEDROCK_ROLE and cfg.bedrock_resource_arns == ()
    assert cfg.conversation_producer == "interviewer_bedrock"


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"env": "staging"}, "env"),
        ({"env": ""}, "env"),
        ({"certificate_arn": ""}, "certificate_arn"),
        ({"certificate_arn": "not-an-arn"}, "certificate_arn"),
        ({"certificate_arn": CERT.replace("ap-southeast-2", "us-east-1")}, "region"),
        ({"hosted_zone_id": "Z123"}, "domain_name"),
        ({"bedrock_model_id": "m"}, "bedrock_resource_arns"),
        ({"bedrock_model_id": "m", "bedrock_resource_arns": "nope"}, "ARNs"),
        (
            {"bedrock_model_id": "m", "bedrock_resource_arns": BEDROCK_ARNS, "bedrock_region": "x"},
            "bedrock_region",
        ),
        ({"bedrock_role_arn": "nope", "bedrock_model_id": "m"}, "IAM role ARN"),
        ({"bedrock_role_arn": BEDROCK_ROLE}, "bedrock_model_id"),
        # full profile (BASE): the tasks have no route to STS
        ({"bedrock_role_arn": BEDROCK_ROLE, "bedrock_model_id": "m"}, "minimal profile"),
        ({"env": "prod"}, "alarm_email"),
        ({"bedrock_log_error_text": "maybe"}, "bedrock_log_error_text"),
        ({"alarm_email": "nope"}, "alarm_email"),
        ({"enable_email": "true"}, "ses_identity_arn"),
        ({"enable_email": "yes"}, "enable_email"),
        ({"web_desired_count": "0"}, "web_desired_count"),
        ({"web_desired_count": "two"}, "web_desired_count"),
        ({"app_name": "x" * 81}, "app_name"),
        ({"availability_zones": "ap-southeast-2a"}, "availability_zones"),
        ({"monthly_budget_usd": "0"}, "monthly_budget_usd"),
    ],
)
def test_invalid_values_are_rejected(overrides, needle):
    with pytest.raises(ConfigError, match=needle):
        load(**overrides)


def test_missing_image_tag_is_reported_by_require_image_tag():
    cfg = load(image_tag="")
    assert cfg.image_tag is None
    with pytest.raises(ConfigError, match="image_tag"):
        cfg.require_image_tag()


def test_oidc_config_defaults_and_validation():
    cfg = OidcConfig.from_context(lambda _: None)
    assert cfg.github_repo == "BlueSky-Digital-Labs/bhp-plan-on-page-generator"
    assert cfg.environments == ("test", "prod") and cfg.cdk_qualifier == "hnb659fds"
    with pytest.raises(ConfigError, match="github_repo"):
        OidcConfig.from_context({"github_repo": "not a repo"}.get)
    with pytest.raises(ConfigError, match="github_environments"):
        OidcConfig.from_context({"github_environments": "dev"}.get)


def test_no_account_ids_or_arns_are_hard_coded():
    root = Path(__file__).resolve().parent.parent
    for path in [root / "config.py", root / "app.py", *(root / "stacks").glob("*.py")]:
        text = path.read_text()
        assert not re.search(r"\b\d{12}\b", text), path
        assert "arn:aws:" not in text, path


def test_every_context_key_is_documented():
    root = Path(__file__).resolve().parent.parent
    source = "".join(p.read_text() for p in [root / "config.py", root / "app.py"])
    keys = set(re.findall(r'(?:_text|_required|_int|_bool|_list|_branding)\(get, "(\w+)"', source))
    keys |= set(re.findall(r'try_get_context\("(\w+)"\)', source))
    assert len(keys) >= 25
    missing = [k for k in keys if not re.search(rf"^{k}\s", config.__doc__, re.MULTILINE)]
    assert not missing
