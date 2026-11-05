import json

import pytest

from tests.conftest import as_list, j, role_statements, stacks

SUB = "repo:BlueSky-Digital-Labs/bhp-plan-on-page-generator:environment:{env}"


def test_provider_is_created_for_github():
    s = stacks()
    s.oidc.has_resource_properties(
        "AWS::IAM::OIDCProvider",
        {
            "Url": "https://token.actions.githubusercontent.com",
            "ClientIdList": ["sts.amazonaws.com"],
        },
    )


def test_existing_provider_can_be_reused():
    arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    s = stacks(github_oidc_provider_arn=arn)
    s.oidc.resource_count_is("AWS::IAM::OIDCProvider", 0)
    assert arn in json.dumps(j(s.oidc))


@pytest.mark.parametrize("env", ["test", "prod"])
def test_each_role_trusts_only_its_github_environment(env):
    s = stacks()
    roles = {
        r["Properties"]["RoleName"]: r["Properties"]
        for r in s.oidc.find_resources("AWS::IAM::Role").values()
    }
    trust = roles[f"plan-github-deploy-{env}"]["AssumeRolePolicyDocument"]["Statement"]
    assert len(trust) == 1
    st = trust[0]
    assert st["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert st["Condition"] == {
        "StringEquals": {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            "token.actions.githubusercontent.com:sub": SUB.format(env=env),
        }
    }


def test_exactly_the_two_roles_exist():
    s = stacks()
    names = {r["Properties"]["RoleName"] for r in s.oidc.find_resources("AWS::IAM::Role").values()}
    assert names == {"plan-github-deploy-test", "plan-github-deploy-prod"}


@pytest.mark.parametrize("env", ["test", "prod"])
def test_permissions_are_narrow(env):
    s = stacks()
    statements = role_statements(s.oidc, f"plan-github-deploy-{env}")
    by_sid = {st["Sid"]: st for st in statements}
    allowed = {a for st in statements for a in as_list(st["Action"])}

    assert not any(a == "*" or a.endswith(":*") for a in allowed)
    assert allowed == {
        "sts:AssumeRole",
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
        "ecr:DescribeImages",
        "cloudformation:DescribeStacks",
        "secretsmanager:DeleteSecret",
        "tag:GetResources",
        "logs:DescribeLogGroups",
        "logs:DeleteLogGroup",
    }
    assume = json.dumps(by_sid["AssumeCdkBootstrapRoles"]["Resource"])
    assert "role/cdk-hnb659fds-deploy-role-" in assume and "file-publishing-role" in assume
    # Only its own repository, stacks and secrets.
    assert f"repository/plan-{env}" in json.dumps(by_sid["EcrPushPullOwnRepository"]["Resource"])
    stack_arns = json.dumps(by_sid["ReadOwnStacks"]["Resource"])
    assert f"stack/plan-{env}-foundation/*" in stack_arns
    assert f"stack/plan-{env}-service/*" in stack_arns
    assert f"secret:plan-{env}/*" in json.dumps(by_sid["PurgeOwnDeletedSecrets"]["Resource"])
    delete = json.dumps(by_sid["PurgeOwnLambdaLogGroups"]["Resource"])
    assert f"log-group:/aws/lambda/plan-{env}-*" in delete
    other = "prod" if env == "test" else "test"
    assert f"plan-{other}" not in json.dumps(statements)
    # Only the two account-wide APIs that cannot be scoped may use "*".
    wildcards = {st["Sid"] for st in statements if st["Resource"] == "*"}
    assert wildcards == {"EcrLogin", "FindTaggedResources"}


def test_custom_qualifier_and_repo():
    s = stacks(cdk_qualifier="plan1", github_repo="Acme/other")
    body = json.dumps(j(s.oidc))
    assert "cdk-plan1-deploy-role" in body and "repo:Acme/other:environment:test" in body
