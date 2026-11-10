"""The minimal profile: a cheap, test-only deployment for a shared account."""

import ast
import json
from pathlib import Path

import pytest
from aws_cdk.assertions import Annotations, Match

from app import build_app
from config import Config, ConfigError
from tests.conftest import (
    BASE,
    BEDROCK_ARNS,
    BEDROCK_ROLE,
    MINIMAL,
    as_list,
    j,
    role_actions,
    role_statements,
    stacks,
)
from tests.test_service import by_description, container, env_of, resources, rules

DYNAMO_PY = Path(__file__).resolve().parents[2] / "backend" / "app" / "dynamo.py"
ROLES = (
    "plan-test-web-task",
    "plan-test-sweeper-task",
    "plan-test-execution",
    "plan-test-scheduler",
)


def minimal(**overrides: str):
    return stacks(profile="", **overrides)  # an empty value means "the default for this env"


@pytest.fixture(scope="module")
def m():
    return minimal()


def all_types(s) -> set[str]:
    return {r["Type"] for t in (s.foundation, s.service) for r in j(t)["Resources"].values()}


# --- selecting the profile ---------------------------------------------------------------------


def get(ctx):
    return lambda key: ctx.get(key)


def test_default_profile_depends_on_the_environment():
    assert Config.from_context(get({**MINIMAL})).profile == "minimal"
    prod = {**MINIMAL, "env": "prod", "alarm_email": "ops@example.com"}
    assert Config.from_context(get(prod)).profile == "full"
    assert Config.from_context(get({**MINIMAL, "profile": "full"})).profile == "full"


def test_minimal_is_refused_for_prod():
    ctx = {**MINIMAL, "env": "prod", "alarm_email": "ops@example.com", "profile": "minimal"}
    with pytest.raises(ConfigError, match="test only"):
        Config.from_context(get(ctx))
    with pytest.raises(ConfigError, match="test only"):
        build_app(ctx)


def test_unknown_profile_is_refused():
    with pytest.raises(ConfigError, match="profile"):
        Config.from_context(get({**MINIMAL, "profile": "tiny"}))


def test_full_profile_for_test_still_has_the_full_design():
    s = stacks()  # BASE says profile=full
    assert {"AWS::WAFv2::WebACL", "AWS::KMS::Key", "AWS::EC2::VPCEndpoint"} <= all_types(s)
    assert BASE["profile"] == "full"


# --- what is not created -----------------------------------------------------------------------


def test_nothing_expensive_or_optional_is_created(m):
    types = all_types(m)
    banned = (
        "AWS::WAFv2::",
        "AWS::KMS::",
        "AWS::EC2::VPCEndpoint",
        "AWS::EC2::NatGateway",
        "AWS::EC2::EIP",
        "AWS::ApplicationAutoScaling::",
        "AWS::SNS::",
        "AWS::CloudWatch::",
        "AWS::Events::",
        "AWS::Budgets::",
    )
    assert not [t for t in types if t.startswith(banned)]
    assert "Custom::VpcRestrictDefaultSG" not in types


def test_expected_resources_remain(m):
    assert {
        "AWS::ECR::Repository",
        "AWS::DynamoDB::Table",
        "AWS::SecretsManager::Secret",
        "AWS::Logs::LogGroup",
        "AWS::ECS::Cluster",
        "AWS::ECS::Service",
        "AWS::Scheduler::Schedule",
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
    } <= all_types(m)
    m.foundation.resource_count_is("AWS::SecretsManager::Secret", 2)
    m.foundation.resource_count_is("AWS::Logs::LogGroup", 2)
    m.service.resource_count_is("AWS::ECS::TaskDefinition", 2)


def test_no_customer_managed_key_anywhere(m):
    table = next(iter(resources(m.foundation, "AWS::DynamoDB::Table").values()))["Properties"]
    assert table.get("SSESpecification", {}).get("SSEType") != "KMS"
    for r in resources(m.foundation, "AWS::SecretsManager::Secret").values():
        assert "KmsKeyId" not in r["Properties"]
    for r in resources(m.foundation, "AWS::Logs::LogGroup").values():
        assert "KmsKeyId" not in r["Properties"] and r["Properties"]["RetentionInDays"] == 7
    assert "kms" not in json.dumps(j(m.foundation)).lower().replace("kmskeyid", "")
    assert "kms:" not in json.dumps(j(m.service))
    assert m.foundation.to_json()["Outputs"].keys() >= {"RepositoryName", "SessionsTableName"}


def test_no_role_has_a_kms_action(m):
    for name in ROLES:
        assert not any(a.startswith("kms:") for a in role_actions(m.service, name)), name
    for r in resources(m.service, "AWS::IAM::Policy").values():
        for st in r["Properties"]["PolicyDocument"]["Statement"]:
            assert not any(a.startswith("kms:") for a in as_list(st["Action"]))


def test_everything_is_still_removed_on_destroy(m):
    for template in (m.foundation, m.service):
        for logical_id, r in j(template)["Resources"].items():
            if r["Type"] != "AWS::CDK::Metadata":
                assert r.get("DeletionPolicy", "Delete") == "Delete", logical_id
                assert r.get("UpdateReplacePolicy", "Delete") == "Delete", logical_id


# --- network -----------------------------------------------------------------------------------


def test_public_subnets_only(m):
    t = m.service
    subnets = resources(t, "AWS::EC2::Subnet")
    assert len(subnets) == 2 and all("public" in k for k in subnets)
    assert {s["Properties"]["AvailabilityZone"] for s in subnets.values()} == {
        "ap-southeast-2a",
        "ap-southeast-2b",
    }
    t.resource_count_is("AWS::EC2::InternetGateway", 1)
    routes = resources(t, "AWS::EC2::Route")
    assert len(routes) == 2 and all("GatewayId" in r["Properties"] for r in routes.values())
    assert not [k for k in resources(t, "AWS::EC2::RouteTable") if "private" in k]


def test_tasks_and_sweeper_run_in_public_subnets_with_public_ips(m):
    t = m.service
    service = next(iter(resources(t, "AWS::ECS::Service").values()))["Properties"]
    vpc = service["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert vpc["AssignPublicIp"] == "ENABLED"
    assert vpc["Subnets"] and all("public" in s["Ref"] for s in vpc["Subnets"])

    sched = next(iter(resources(t, "AWS::Scheduler::Schedule").values()))["Properties"]
    sweeper = sched["Target"]["EcsParameters"]["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert sweeper["AssignPublicIp"] == "ENABLED"
    assert all("public" in json.dumps(s) for s in as_list(sweeper["Subnets"]))
    assert "TaskSg" in json.dumps(sweeper["SecurityGroups"])
    assert m.service.to_json()["Outputs"]["TaskAssignPublicIp"]["Value"] == "ENABLED"


def test_task_security_group_admits_only_the_alb(m):
    t = m.service
    alb_id, _ = by_description(t, "Public ALB")
    task_id, _ = by_description(t, "Fargate tasks")
    t.resource_count_is("AWS::EC2::SecurityGroup", 2)  # no endpoint group
    ingress = rules(t, task_id, "Ingress")
    assert [(r["FromPort"], r["ToPort"], r["SourceSecurityGroupId"]) for r in ingress] == [
        (8000, 8000, {"Fn::GetAtt": [alb_id, "GroupId"]})
    ]
    assert not any("CidrIp" in r or "CidrIpv6" in r for r in ingress)
    egress = {(r["IpProtocol"], r["FromPort"], r["CidrIp"]) for r in rules(t, task_id, "Egress")}
    assert egress == {("tcp", 443, "0.0.0.0/0"), ("udp", 53, "0.0.0.0/0"), ("tcp", 53, "0.0.0.0/0")}
    alb_in = {(r["FromPort"], r["CidrIp"]) for r in rules(t, alb_id, "Ingress")}
    assert alb_in == {(443, "0.0.0.0/0"), (80, "0.0.0.0/0")}


def test_alb_is_as_designed(m):
    t = m.service
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        {
            "Scheme": "internet-facing",
            "LoadBalancerAttributes": Match.array_with(
                [
                    {"Key": "idle_timeout.timeout_seconds", "Value": "120"},
                    {"Key": "routing.http.drop_invalid_header_fields.enabled", "Value": "true"},
                ]
            ),
        },
    )
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::Listener",
        {
            "Port": 443,
            "Protocol": "HTTPS",
            "SslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
            "Certificates": [{"CertificateArn": BASE["certificate_arn"]}],
        },
    )
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::Listener",
        {
            "Port": 80,
            "DefaultActions": [Match.object_like({"Type": "redirect"})],
        },
    )
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {"HealthCheckPath": "/api/health", "Port": 8000},
    )
    alb = next(iter(resources(t, "AWS::ElasticLoadBalancingV2::LoadBalancer").values()))
    assert all("public" in s["Ref"] for s in alb["Properties"]["Subnets"])


# --- tasks -------------------------------------------------------------------------------------


def test_one_task_and_no_autoscaling(m):
    m.service.has_resource_properties("AWS::ECS::Service", {"DesiredCount": 1})
    m.service.resource_count_is("AWS::ApplicationAutoScaling::ScalableTarget", 0)
    s = minimal(web_desired_count="2")
    s.service.has_resource_properties("AWS::ECS::Service", {"DesiredCount": 2})
    s.service.resource_count_is("AWS::ApplicationAutoScaling::ScalableTarget", 0)


def test_service_keeps_its_deployment_safety(m):
    m.service.has_resource_properties(
        "AWS::ECS::Service",
        {
            "HealthCheckGracePeriodSeconds": 60,
            "DeploymentConfiguration": Match.object_like(
                {"DeploymentCircuitBreaker": {"Enable": True, "Rollback": True}}
            ),
        },
    )


@pytest.mark.parametrize("family", ["plan-test-web", "plan-test-sweeper"])
def test_containers_are_unchanged(m, family):
    task, c = container(m.service, family)
    assert c["ReadonlyRootFilesystem"] is True
    assert c["MountPoints"] == [{"ContainerPath": "/tmp", "ReadOnly": False, "SourceVolume": "tmp"}]
    assert "Secrets" not in c
    env = env_of(c)
    assert env["APP_ENV"] == "deployed" and env["ACCESS_CODE_HASH_SECRET_ID"] == (
        "plan-test/access-code-hash"
    )


def test_sweeper_command_and_schedule_are_unchanged(m):
    _, c = container(m.service, "plan-test-sweeper")
    assert c["Command"] == ["python", "-m", "app.expiry"]
    sched = next(iter(resources(m.service, "AWS::Scheduler::Schedule").values()))["Properties"]
    assert sched["ScheduleExpression"] == "rate(5 minutes)"


# --- roles -------------------------------------------------------------------------------------

WEB_DDB = {
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:Query",
    "dynamodb:BatchWriteItem",
    "dynamodb:ConditionCheckItem",
}


def test_web_role_without_kms(m):
    assert role_actions(m.service, "plan-test-web-task") == WEB_DDB | {
        "secretsmanager:GetSecretValue"
    }
    by_sid = {s["Sid"]: s for s in role_statements(m.service, "plan-test-web-task")}
    assert len(by_sid["SessionsTable"]["Resource"]) == 2
    secrets = by_sid["AccessSecrets"]["Resource"]
    assert len(secrets) == 2 and "*" not in json.dumps(secrets)


def test_web_role_bedrock_is_stream_only_on_the_given_arns():
    s = minimal(bedrock_model_id="au.example-model-v1:0", bedrock_resource_arns=BEDROCK_ARNS)
    stmt = next(
        x
        for x in role_statements(s.service, "plan-test-web-task")
        if any(a.startswith("bedrock:") for a in as_list(x["Action"]))
    )
    assert as_list(stmt["Action"]) == ["bedrock:InvokeModelWithResponseStream"]
    assert stmt["Resource"] == BEDROCK_ARNS.split(",")
    assert not any(a.startswith(("ses:", "kms:")) for a in role_actions(s.service, ROLES[0]))
    assert env_of(container(s.service, "plan-test-web")[1])["CONVERSATION_PRODUCER"] == (
        "interviewer_bedrock"
    )
    assert not any(a.startswith("bedrock:") for a in role_actions(s.service, ROLES[1]))


def test_a_bedrock_role_is_assumed_and_the_web_role_has_no_bedrock_actions_of_its_own():
    s = minimal(bedrock_model_id="au.example-model-v1:0", bedrock_role_arn=BEDROCK_ROLE)
    by_sid = {x["Sid"]: x for x in role_statements(s.service, "plan-test-web-task")}
    assert as_list(by_sid["AssumeBedrockRole"]["Action"]) == ["sts:AssumeRole"]
    assert as_list(by_sid["AssumeBedrockRole"]["Resource"]) == [BEDROCK_ROLE]
    assert "InvokeApprovedModel" not in by_sid
    assert not any(a.startswith("bedrock:") for a in role_actions(s.service, ROLES[0]))
    assert not any(a.startswith("sts:") for a in role_actions(s.service, ROLES[1]))
    web_env = env_of(container(s.service, "plan-test-web")[1])
    assert web_env["AWS_CONFIG_FILE"] == "/app/aws-config"
    assert web_env["BEDROCK_PROFILE"] == "rnd-bedrock"
    assert web_env["CONVERSATION_PRODUCER"] == "interviewer_bedrock"
    sweeper_env = env_of(container(s.service, "plan-test-sweeper")[1])
    assert "BEDROCK_PROFILE" not in sweeper_env and "AWS_CONFIG_FILE" not in sweeper_env


def test_bedrock_error_text_logging_is_off_unless_asked_for(m):
    assert "BEDROCK_LOG_ERROR_TEXT" not in env_of(container(m.service, "plan-test-web")[1])
    on = minimal(
        bedrock_model_id="au.example-model-v1:0",
        bedrock_resource_arns=BEDROCK_ARNS,
        bedrock_log_error_text="true",
    )
    assert env_of(container(on.service, "plan-test-web")[1])["BEDROCK_LOG_ERROR_TEXT"] == "true"
    assert "BEDROCK_LOG_ERROR_TEXT" not in env_of(container(on.service, "plan-test-sweeper")[1])
    warnings = Annotations.from_stack(on.service_stack).find_warning(
        "*", Match.string_like_regexp("error text|validation errors")
    )
    assert warnings


def test_the_recipient_and_text_are_read_from_two_parameters_at_run_time():
    mail = {
        "enable_email": "true",
        "ses_identity_arn": "arn:aws:ses:ap-southeast-2:123456789012:identity/example.com",
        "email_sender_address": "noreply@example.com",
        "email_allowed_domains": "example.com",
    }
    s = minimal(**mail)
    by_sid = {x["Sid"]: x for x in role_statements(s.service, "plan-test-web-task")}
    read = by_sid["ReadEmailSettings"]
    assert as_list(read["Action"]) == ["ssm:GetParameters"]
    resources = json.dumps(read["Resource"])
    assert resources.count("parameter/plan-test/email-recipient") == 1
    assert resources.count("parameter/plan-test/email-body") == 1
    assert "*" not in resources and len(as_list(read["Resource"])) == 2
    env = env_of(container(s.service, "plan-test-web")[1])
    assert env["EMAIL_RECIPIENT_PARAMETER"] == "/plan-test/email-recipient"
    assert env["EMAIL_BODY_PARAMETER"] == "/plan-test/email-body"
    sweeper = env_of(container(s.service, "plan-test-sweeper")[1])
    assert "EMAIL_RECIPIENT_PARAMETER" not in sweeper
    assert not any(a.startswith("ssm:") and "email" in a for a in role_actions(s.service, ROLES[1]))
    off = {x["Sid"] for x in role_statements(minimal().service, "plan-test-web-task")}
    assert "ReadEmailSettings" not in off


def test_email_error_text_logging_is_off_unless_asked_for():
    mail = {
        "enable_email": "true",
        "ses_identity_arn": "arn:aws:ses:ap-southeast-2:123456789012:identity/example.com",
        "email_sender_address": "noreply@example.com",
        "email_allowed_domains": "example.com",
    }
    off = minimal(**mail)
    assert "EMAIL_LOG_ERROR_TEXT" not in env_of(container(off.service, "plan-test-web")[1])
    on = minimal(**mail, email_log_error_text="true")
    assert env_of(container(on.service, "plan-test-web")[1])["EMAIL_LOG_ERROR_TEXT"] == "true"
    assert "EMAIL_LOG_ERROR_TEXT" not in env_of(container(on.service, "plan-test-sweeper")[1])


def test_the_role_must_match_the_one_baked_into_the_image():
    other = "arn:aws:iam::667736131634:role/someone-else"
    with pytest.raises(ValueError, match="aws-config"):
        minimal(bedrock_model_id="au.example-model-v1:0", bedrock_role_arn=other)


def test_the_task_role_name_is_the_one_the_other_account_trusts(m):
    assert ROLES[0] == "plan-test-web-task"
    assert any(
        r["Properties"].get("RoleName") == "plan-test-web-task"
        for r in resources(m.service, "AWS::IAM::Role").values()
    )


def test_without_a_model_there_is_no_bedrock_access(m):
    assert not any(a.startswith("bedrock:") for a in role_actions(m.service, ROLES[0]))


def test_sweeper_role_is_four_dynamodb_actions_only(m):
    assert role_actions(m.service, "plan-test-sweeper-task") == {
        "dynamodb:Query",
        "dynamodb:DeleteItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:GetItem",
    }


def test_execution_and_scheduler_roles_are_unchanged(m):
    assert role_actions(m.service, "plan-test-execution") == {
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:GetAuthorizationToken",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    assert role_actions(m.service, "plan-test-scheduler") == {"ecs:RunTask", "iam:PassRole"}


def test_email_is_allowed_and_not_flagged_because_tasks_have_internet():
    ctx = {
        **MINIMAL,
        "enable_email": "true",
        "ses_identity_arn": "arn:aws:ses:ap-southeast-2:123456789012:identity/x",
        "email_sender_address": "plan@example.com",
        "email_allowed_domains": "example.com",
    }
    app = build_app(ctx)
    service = app.node.find_child("plan-test-service")
    assert not Annotations.from_stack(service).find_warning("*", Match.string_like_regexp("SES"))
    s = stacks(profile="", **{k: v for k, v in ctx.items() if k not in MINIMAL})
    stmt = next(
        x
        for x in role_statements(s.service, ROLES[0])
        if as_list(x["Action"]) == ["ses:SendEmail", "ses:SendRawEmail"]
    )
    assert stmt["Condition"] == {"StringEquals": {"ses:FromAddress": "plan@example.com"}}


# --- alarms only on request ---------------------------------------------------------------------


def test_alarm_email_brings_back_alarms_rule_and_budget():
    s = minimal(alarm_email="ops@example.com")
    s.service.resource_count_is("AWS::SNS::Topic", 1)
    s.service.resource_count_is("AWS::CloudWatch::Alarm", 4)
    s.service.resource_count_is("AWS::Events::Rule", 1)
    s.service.resource_count_is("AWS::Budgets::Budget", 1)
    s.service.has_resource_properties(
        "AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "ops@example.com"}
    )
    s.service.resource_count_is("AWS::WAFv2::WebACL", 0)  # still no WAF


# --- the table is the same table ---------------------------------------------------------------


def table(s) -> dict:
    return next(iter(resources(s.foundation, "AWS::DynamoDB::Table").values()))["Properties"]


def test_table_definition_equals_the_full_profiles(m):
    full = stacks()
    ignore = {"SSESpecification"}  # the only difference: AWS-owned key instead of the CMK
    a = {k: v for k, v in table(m).items() if k not in ignore}
    b = {k: v for k, v in table(full).items() if k not in ignore}
    a.pop("Tags", None), b.pop("Tags", None)
    assert a == b
    assert table(full)["SSESpecification"]["SSEType"] == "KMS"
    assert "SSEType" not in table(m).get("SSESpecification", {})  # AWS-owned key
    assert table(m)["PointInTimeRecoverySpecification"] == {"PointInTimeRecoveryEnabled": False}


def backend_table_definition(name: str) -> dict:
    """`table_definition` from backend/app/dynamo.py, evaluated without importing the backend."""
    tree = ast.parse(DYNAMO_PY.read_text())
    constants = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "table_definition"
    )
    function.returns = None
    function.args.args[0].annotation = None
    namespace: dict = dict(constants)
    exec(compile(ast.Module([function], []), str(DYNAMO_PY), "exec"), namespace)  # noqa: S102
    return namespace["table_definition"](name)


@pytest.mark.parametrize("profile", ["full", "minimal"])
def test_table_matches_the_backend_contract(profile):
    s = stacks(profile=profile)
    expected = backend_table_definition(table(s)["TableName"])
    actual = table(s)
    assert actual["BillingMode"] == expected["BillingMode"]
    assert actual["KeySchema"] == expected["KeySchema"]
    assert actual["GlobalSecondaryIndexes"] == expected["GlobalSecondaryIndexes"]
    key = lambda d: d["AttributeName"]  # noqa: E731
    assert sorted(actual["AttributeDefinitions"], key=key) == sorted(
        expected["AttributeDefinitions"], key=key
    )
    assert actual["TimeToLiveSpecification"] == {"AttributeName": "expires_at", "Enabled": True}
