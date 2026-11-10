"""The Parameter Store secrets backend, for accounts that deny secretsmanager:CreateSecret."""

import json

import pytest

from config import Config, ConfigError
from tests.conftest import BASE, MINIMAL, as_list, j, role_actions, role_statements, stacks
from tests.test_service import container, env_of, resources

FULL_PS = {"profile": "full", "secrets_backend": "parameterstore"}
ROLES = (
    "plan-test-web-task",
    "plan-test-sweeper-task",
    "plan-test-execution",
    "plan-test-scheduler",
)


def get(ctx):
    return lambda key: ctx.get(key)


def ps_minimal():
    return stacks(profile="", secrets_backend="parameterstore")


def ps_full():
    return stacks(**FULL_PS)


# --- selecting the backend ---------------------------------------------------------------------


def test_default_backend_follows_the_profile():
    base = {k: v for k, v in BASE.items() if k != "secrets_backend"}
    assert Config.from_context(get({**base, "profile": "full"})).secrets_backend == "secretsmanager"
    assert Config.from_context(get({**base, "profile": "minimal"})).secrets_backend == (
        "parameterstore"
    )
    assert Config.from_context(get({**base, "profile": ""})).secrets_backend == "parameterstore"
    prod = {**base, "env": "prod", "alarm_email": "ops@example.com", "profile": ""}
    assert Config.from_context(get(prod)).secrets_backend == "secretsmanager"


def test_either_backend_can_be_set_on_either_profile():
    for profile in ("full", "minimal"):
        for backend in ("secretsmanager", "parameterstore"):
            ctx = {**BASE, "profile": profile, "secrets_backend": backend}
            assert Config.from_context(get(ctx)).secrets_backend == backend


def test_an_unknown_backend_is_refused():
    with pytest.raises(ConfigError, match="secrets_backend"):
        Config.from_context(get({**BASE, "secrets_backend": "vault"}))


def test_identifiers_carry_the_leading_slash_only_for_parameters():
    ps = Config.from_context(get({**BASE, "secrets_backend": "parameterstore"}))
    sm = Config.from_context(get(BASE))
    assert (ps.access_code_id, ps.cookie_key_id) == (
        "/plan-test/access-code-hash",
        "/plan-test/cookie-signing-key",
    )
    assert (sm.access_code_id, sm.cookie_key_id) == (
        "plan-test/access-code-hash",
        "plan-test/cookie-signing-key",
    )


@pytest.mark.parametrize("make", [ps_minimal, ps_full])
def test_both_profiles_synthesise_with_parameter_store(make):
    s = make()
    assert s.foundation is not None and s.service is not None


def test_minimal_with_secrets_manager_still_synthesises():
    s = stacks(profile="", secrets_backend="secretsmanager")
    s.foundation.resource_count_is("AWS::SecretsManager::Secret", 2)


# --- foundation --------------------------------------------------------------------------------


@pytest.mark.parametrize("make", [ps_minimal, ps_full])
def test_foundation_has_no_secret_and_no_parameter_resource(make):
    s = make()
    types = {r["Type"] for r in j(s.foundation)["Resources"].values()}
    assert not [t for t in types if t.startswith(("AWS::SecretsManager::", "AWS::SSM::"))]
    outputs = j(s.foundation)["Outputs"]
    assert outputs["AccessCodeHashParameter"]["Value"] == "/plan-test/access-code-hash"
    assert outputs["CookieSigningKeyParameter"]["Value"] == "/plan-test/cookie-signing-key"
    assert "AccessCodeSecretName" not in outputs and "CookieKeySecretName" not in outputs
    for template in (s.foundation, s.service):
        assert "SecretString" not in json.dumps(j(template))


def test_the_rest_of_the_foundation_is_unchanged():
    a, b = ps_minimal(), stacks(profile="")
    assert {r["Type"] for r in j(a.foundation)["Resources"].values()} | {
        "AWS::SecretsManager::Secret"
    } == {r["Type"] for r in j(b.foundation)["Resources"].values()}


# --- roles -------------------------------------------------------------------------------------


@pytest.mark.parametrize("make", [ps_minimal, ps_full])
def test_web_role_reads_exactly_two_parameters(make):
    s = make()
    statements = role_statements(s.service, "plan-test-web-task")
    ssm = [st for st in statements if any(a.startswith("ssm:") for a in as_list(st["Action"]))]
    assert len(ssm) == 1 and ssm[0]["Action"] == "ssm:GetParameter"
    arns = ssm[0]["Resource"]
    assert len(arns) == 2 and "*" not in json.dumps(arns)
    text = json.dumps(arns)
    assert ":parameter/plan-test/access-code-hash" in text
    assert ":parameter/plan-test/cookie-signing-key" in text
    assert "ssm" in text and "ap-southeast-2" in text and "AWS::AccountId" in text
    assert "Condition" not in ssm[0]


@pytest.mark.parametrize("make", [ps_minimal, ps_full])
def test_no_role_has_another_ssm_or_any_secretsmanager_action(make):
    s = make()
    seen = set()
    for name in ROLES:
        for action in role_actions(s.service, name):
            assert not action.startswith("secretsmanager:"), (name, action)
            if action.startswith("ssm:"):
                seen.add((name, action))
    assert seen == {("plan-test-web-task", "ssm:GetParameter")}  # the sweeper has none


def test_kms_follows_the_profile():
    full = role_actions(ps_full().service, "plan-test-web-task")
    assert {"kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"} <= full  # covers a CMK key
    minimal = role_actions(ps_minimal().service, "plan-test-web-task")
    assert not any(a.startswith("kms:") for a in minimal)  # alias/aws/ssm needs no statement


def test_web_role_keeps_its_other_permissions():
    actions = role_actions(ps_minimal().service, "plan-test-web-task")
    assert actions == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:Query",
        "dynamodb:BatchWriteItem",
        "dynamodb:ConditionCheckItem",
        "ssm:GetParameter",
    }


# --- containers --------------------------------------------------------------------------------


@pytest.mark.parametrize("make", [ps_minimal, ps_full])
@pytest.mark.parametrize("family", ["plan-test-web", "plan-test-sweeper"])
def test_both_containers_name_the_backend_and_the_parameters(make, family):
    env = env_of(container(make().service, family)[1])
    assert env["SECRETS_BACKEND"] == "parameterstore"
    assert env["ACCESS_CODE_HASH_SECRET_ID"] == "/plan-test/access-code-hash"
    assert env["COOKIE_SIGNING_KEY_SECRET_ID"] == "/plan-test/cookie-signing-key"
    assert env["APP_ENV"] == "deployed" and "SESSIONS_TABLE_NAME" in env


def test_secrets_manager_containers_keep_the_app_default():
    for family in ("plan-test-web", "plan-test-sweeper"):
        env = env_of(container(stacks().service, family)[1])
        assert "SECRETS_BACKEND" not in env  # secretsmanager is the app's default
        assert env["ACCESS_CODE_HASH_SECRET_ID"] == "plan-test/access-code-hash"


def test_no_container_secrets_injection():
    for family in ("plan-test-web", "plan-test-sweeper"):
        assert "Secrets" not in container(ps_minimal().service, family)[1]


# --- network -----------------------------------------------------------------------------------


def endpoint_names(template) -> list[str]:
    known = ("bedrock-runtime", "secretsmanager", "ecr.api", "ecr.dkr", "logs", "ssm")
    out = []
    for v in resources(template, "AWS::EC2::VPCEndpoint").values():
        if v["Properties"]["VpcEndpointType"] == "Interface":
            text = json.dumps(v["Properties"]["ServiceName"])
            out.append(next(n for n in known if f".{n}" in text or n == text.strip('"')))
    return sorted(out)


def test_full_profile_reaches_ssm_through_an_endpoint_instead_of_secrets_manager():
    names = endpoint_names(ps_full().service)
    assert names == ["bedrock-runtime", "ecr.api", "ecr.dkr", "logs", "ssm"]
    assert endpoint_names(stacks().service) == [
        "bedrock-runtime",
        "ecr.api",
        "ecr.dkr",
        "logs",
        "secretsmanager",
    ]


def test_minimal_still_has_no_endpoints():
    ps_minimal().service.resource_count_is("AWS::EC2::VPCEndpoint", 0)
    assert MINIMAL["secrets_backend"] == "secretsmanager"
