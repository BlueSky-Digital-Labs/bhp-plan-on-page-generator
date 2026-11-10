import json

import pytest
from aws_cdk.assertions import Annotations, Match

from app import build_app
from config import ConfigError
from stacks.foundation import FoundationStack
from stacks.service import ServiceStack
from tests.conftest import (
    BASE,
    BEDROCK_ARNS,
    CERT,
    as_list,
    j,
    role_actions,
    role_statements,
    stacks,
)


def resources(template, type_):
    return {k: v for k, v in j(template)["Resources"].items() if v["Type"] == type_}


def by_description(template, description):
    found = {
        k: v
        for k, v in resources(template, "AWS::EC2::SecurityGroup").items()
        if v["Properties"]["GroupDescription"] == description
    }
    assert len(found) == 1, description
    return next(iter(found.items()))


def rules(template, group_id, direction):
    """Ingress or egress rules of one security group, inline and as separate resources."""
    prop = f"SecurityGroup{direction}"
    out = list(resources(template, "AWS::EC2::SecurityGroup")[group_id]["Properties"].get(prop, []))
    for r in resources(template, f"AWS::EC2::{prop}").values():
        p = r["Properties"]
        if direction == "Ingress" and p["GroupId"] == {"Fn::GetAtt": [group_id, "GroupId"]}:
            out.append(p)
        if direction == "Egress" and p["GroupId"] == {"Fn::GetAtt": [group_id, "GroupId"]}:
            out.append(p)
    return [r for r in out if r.get("CidrIp") != "255.255.255.255/32"]  # CDK's "deny all" stub


# --- network -----------------------------------------------------------------------------------


def test_no_nat_and_no_internet_route_from_private_subnets(test_env):
    t = test_env.service
    t.resource_count_is("AWS::EC2::NatGateway", 0)
    t.resource_count_is("AWS::EC2::EIP", 0)
    t.resource_count_is("AWS::EC2::VPC", 1)
    t.resource_count_is("AWS::EC2::InternetGateway", 1)
    routes = resources(t, "AWS::EC2::Route")
    assert len(routes) == 2  # one default route per public subnet, nothing else
    for r in routes.values():
        p = r["Properties"]
        assert "NatGatewayId" not in p and "GatewayId" in p
        assert "public" in p["RouteTableId"]["Ref"]
    # Private subnets have route tables with no routes at all.
    private_tables = [k for k in resources(t, "AWS::EC2::RouteTable") if "private" in k]
    assert len(private_tables) == 2
    assert not any(any(k in json.dumps(r) for k in private_tables) for r in routes.values())


def test_two_azs_public_for_alb_only_private_isolated_for_tasks(test_env):
    t = test_env.service
    subnets = resources(t, "AWS::EC2::Subnet")
    assert len(subnets) == 4
    assert {s["Properties"]["AvailabilityZone"] for s in subnets.values()} == {
        "ap-southeast-2a",
        "ap-southeast-2b",
    }
    for key, s in subnets.items():
        if "public" in key:
            assert s["Properties"]["MapPublicIpOnLaunch"] is False
    t.has_resource_properties(
        "AWS::ECS::Service",
        {
            "NetworkConfiguration": {
                "AwsvpcConfiguration": Match.object_like(
                    {"AssignPublicIp": "DISABLED", "Subnets": Match.array_with([])}
                )
            }
        },
    )
    service = next(iter(resources(t, "AWS::ECS::Service").values()))["Properties"]
    for subnet in service["NetworkConfiguration"]["AwsvpcConfiguration"]["Subnets"]:
        assert "private" in subnet["Ref"]
    alb = next(iter(resources(t, "AWS::ElasticLoadBalancingV2::LoadBalancer").values()))
    assert all("public" in s["Ref"] for s in alb["Properties"]["Subnets"])


def test_vpc_endpoints(test_env):
    t = test_env.service
    endpoints = resources(t, "AWS::EC2::VPCEndpoint")
    assert len(endpoints) == 7
    interface = {
        k: v for k, v in endpoints.items() if v["Properties"]["VpcEndpointType"] == "Interface"
    }
    gateway = {k: v for k, v in endpoints.items() if k not in interface}
    known = ("bedrock-runtime", "secretsmanager", "ecr.api", "ecr.dkr", "logs")
    names = sorted(
        next(n for n in known if n in json.dumps(v["Properties"]["ServiceName"]))
        for v in interface.values()
    )
    assert names == ["bedrock-runtime", "ecr.api", "ecr.dkr", "logs", "secretsmanager"]
    _, endpoint_sg = by_description(t, "Interface endpoints")
    for v in interface.values():
        p = v["Properties"]
        assert p["PrivateDnsEnabled"] is True
        assert len(p["SecurityGroupIds"]) == 1
        assert all("private" in s["Ref"] for s in p["SubnetIds"])
    gateway_names = sorted(
        "dynamodb" if "dynamodb" in json.dumps(v["Properties"]["ServiceName"]) else "s3"
        for v in gateway.values()
    )
    assert gateway_names == ["dynamodb", "s3"]
    for v in gateway.values():
        assert v["Properties"]["VpcEndpointType"] == "Gateway"
        assert all("private" in r["Ref"] for r in v["Properties"]["RouteTableIds"])
    assert endpoint_sg


def test_security_group_rules(test_env):
    t = test_env.service
    alb_id, _ = by_description(t, "Public ALB")
    task_id, _ = by_description(t, "Fargate tasks")
    ep_id, _ = by_description(t, "Interface endpoints")

    def gid(group):
        return {"Fn::GetAtt": [group, "GroupId"]}

    ingress = rules(t, alb_id, "Ingress")
    assert {(r["FromPort"], r["CidrIp"]) for r in ingress} == {
        (443, "0.0.0.0/0"),
        (80, "0.0.0.0/0"),
    }
    egress = rules(t, alb_id, "Egress")
    assert [(r["FromPort"], r["DestinationSecurityGroupId"]) for r in egress] == [
        (8000, gid(task_id))
    ]

    # Tasks accept traffic only from the ALB, on the app port.
    task_in = rules(t, task_id, "Ingress")
    assert [(r["FromPort"], r["ToPort"], r["SourceSecurityGroupId"]) for r in task_in] == [
        (8000, 8000, gid(alb_id))
    ]
    assert [(r["FromPort"], r["ToPort"]) for r in rules(t, task_id, "Egress")] == [(443, 443)]

    # Endpoints accept 443 from the task group and nothing else.
    ep_in = rules(t, ep_id, "Ingress")
    assert [(r["FromPort"], r["ToPort"], r["SourceSecurityGroupId"]) for r in ep_in] == [
        (443, 443, gid(task_id))
    ]
    assert rules(t, ep_id, "Egress") == []


# --- load balancer, WAF ------------------------------------------------------------------------


def test_alb_attributes_and_listeners(test_env):
    t = test_env.service
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        {
            "Scheme": "internet-facing",
            "Type": "application",
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
            "Certificates": [{"CertificateArn": CERT}],
            "DefaultActions": [Match.object_like({"Type": "forward"})],
        },
    )
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::Listener",
        {
            "Port": 80,
            "Protocol": "HTTP",
            "DefaultActions": [
                {
                    "Type": "redirect",
                    "RedirectConfig": {
                        "Protocol": "HTTPS",
                        "Port": "443",
                        "StatusCode": "HTTP_301",
                    },
                }
            ],
        },
    )
    t.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::TargetGroup",
        {
            "TargetType": "ip",
            "Port": 8000,
            "HealthCheckPath": "/api/health",
            "Matcher": {"HttpCode": "200"},
        },
    )


def test_waf_rules_and_association(test_env):
    t = test_env.service
    acl = next(iter(resources(t, "AWS::WAFv2::WebACL").values()))["Properties"]
    assert acl["Scope"] == "REGIONAL" and acl["DefaultAction"] == {"Allow": {}}
    rules_ = {r["Name"]: r for r in acl["Rules"]}
    assert set(rules_) == {
        "RateLimitSensitivePaths",
        "RateLimitGeneral",
        "AWSManagedRulesCommonRuleSet",
        "AWSManagedRulesKnownBadInputsRuleSet",
    }
    assert len({r["Priority"] for r in rules_.values()}) == 4
    for name in ("AWSManagedRulesCommonRuleSet", "AWSManagedRulesKnownBadInputsRuleSet"):
        group = rules_[name]["Statement"]["ManagedRuleGroupStatement"]
        assert (group["VendorName"], group["Name"]) == ("AWS", name)
        assert rules_[name]["OverrideAction"] == {"None": {}}
    # Uploads of up to 5 MB must not be blocked by the 8 KB body-size rule.
    common = rules_["AWSManagedRulesCommonRuleSet"]["Statement"]["ManagedRuleGroupStatement"]
    assert common["RuleActionOverrides"] == [
        {"Name": "SizeRestrictions_BODY", "ActionToUse": {"Count": {}}}
    ]

    sensitive = rules_["RateLimitSensitivePaths"]
    rate = sensitive["Statement"]["RateBasedStatement"]
    assert sensitive["Action"] == {"Block": {}}
    assert (rate["Limit"], rate["AggregateKeyType"], rate["EvaluationWindowSec"]) == (
        100,
        "IP",
        300,
    )
    scoped = rate["ScopeDownStatement"]["OrStatement"]["Statements"]
    matches = {
        (s["ByteMatchStatement"]["SearchString"], s["ByteMatchStatement"]["PositionalConstraint"])
        for s in scoped
    }
    assert matches == {("/api/access", "EXACTLY"), ("/api/sessions", "STARTS_WITH")}
    assert all(s["ByteMatchStatement"]["FieldToMatch"] == {"UriPath": {}} for s in scoped)

    general = rules_["RateLimitGeneral"]["Statement"]["RateBasedStatement"]
    assert general["Limit"] > rate["Limit"] and "ScopeDownStatement" not in general

    for r in [*acl["Rules"]]:
        assert r["VisibilityConfig"]["SampledRequestsEnabled"] is False  # samples hold cookies
    t.has_resource_properties(
        "AWS::WAFv2::WebACLAssociation",
        {"ResourceArn": Match.any_value(), "WebACLArn": Match.any_value()},
    )


def test_waf_rate_limits_are_configurable():
    s = stacks(rate_limit_sensitive="30", rate_limit_general="500")
    acl = next(iter(resources(s.service, "AWS::WAFv2::WebACL").values()))["Properties"]
    limits = {
        r["Name"]: r["Statement"]["RateBasedStatement"]["Limit"]
        for r in acl["Rules"]
        if "RateBasedStatement" in r["Statement"]
    }
    assert limits == {"RateLimitSensitivePaths": 30, "RateLimitGeneral": 500}


def test_dns_alias_only_with_zone_and_domain(test_env):
    test_env.service.resource_count_is("AWS::Route53::RecordSet", 0)
    s = stacks(domain_name="plan.example.com", hosted_zone_id="Z0123456789")
    s.service.has_resource_properties(
        "AWS::Route53::RecordSet",
        {"HostedZoneId": "Z0123456789", "Name": "plan.example.com", "Type": "A"},
    )
    assert j(s.service)["Outputs"]["ServiceUrl"]["Value"] == "https://plan.example.com"
    assert "Fn::GetAtt" in json.dumps(j(test_env.service)["Outputs"]["ServiceUrl"])


# --- web service -------------------------------------------------------------------------------


def test_web_service_settings(test_env):
    t = test_env.service
    t.has_resource_properties(
        "AWS::ECS::Service",
        {
            "LaunchType": "FARGATE",
            "DesiredCount": 2,
            "HealthCheckGracePeriodSeconds": 60,
            "DeploymentConfiguration": Match.object_like(
                {
                    "DeploymentCircuitBreaker": {"Enable": True, "Rollback": True},
                    "MinimumHealthyPercent": 100,
                }
            ),
            "AvailabilityZoneRebalancing": "ENABLED",
        },
    )
    t.has_resource_properties(
        "AWS::ApplicationAutoScaling::ScalableTarget", {"MinCapacity": 2, "MaxCapacity": 4}
    )
    t.has_resource_properties(
        "AWS::ApplicationAutoScaling::ScalingPolicy",
        {
            "TargetTrackingScalingPolicyConfiguration": Match.object_like(
                {
                    "TargetValue": 60,
                    "PredefinedMetricSpecification": {
                        "PredefinedMetricType": "ECSServiceAverageCPUUtilization"
                    },
                }
            )
        },
    )


def test_desired_count_is_configurable():
    s = stacks(web_desired_count="3")
    s.service.has_resource_properties("AWS::ECS::Service", {"DesiredCount": 3})
    s.service.has_resource_properties(
        "AWS::ApplicationAutoScaling::ScalableTarget", {"MinCapacity": 3, "MaxCapacity": 4}
    )


def container(template, family):
    for r in resources(template, "AWS::ECS::TaskDefinition").values():
        if r["Properties"]["Family"] == family:
            assert len(r["Properties"]["ContainerDefinitions"]) == 1
            return r["Properties"], r["Properties"]["ContainerDefinitions"][0]
    raise AssertionError(family)


def env_of(c):
    return {e["Name"]: e["Value"] for e in c["Environment"]}


@pytest.mark.parametrize("family", ["plan-test-web", "plan-test-sweeper"])
def test_containers_are_read_only_with_a_tmp_volume(test_env, family):
    task, c = container(test_env.service, family)
    assert c["ReadonlyRootFilesystem"] is True
    assert c["MountPoints"] == [{"ContainerPath": "/tmp", "ReadOnly": False, "SourceVolume": "tmp"}]
    assert task["Volumes"] == [{"Name": "tmp"}]
    assert "Secrets" not in c  # the app reads secrets itself
    assert c["LogConfiguration"]["LogDriver"] == "awslogs"
    assert task["RuntimePlatform"] == {
        "CpuArchitecture": "X86_64",
        "OperatingSystemFamily": "LINUX",
    }
    assert "Privileged" not in c


def test_web_task_definition(test_env):
    task, c = container(test_env.service, "plan-test-web")
    assert (task["Cpu"], task["Memory"]) == ("512", "1024")
    assert c["PortMappings"] == [{"ContainerPort": 8000, "Protocol": "tcp"}]
    assert "Command" not in c
    env = env_of(c)
    assert env["APP_ENV"] == "deployed" and env["AWS_REGION"] == "ap-southeast-2"
    assert env["ACCESS_CODE_HASH_SECRET_ID"] == "plan-test/access-code-hash"
    assert env["COOKIE_SIGNING_KEY_SECRET_ID"] == "plan-test/cookie-signing-key"
    assert "SESSIONS_TABLE_NAME" in env
    assert env["APP_NAME"] == "SCP AI Ideation assistant" and env["FOOTER_TEXT"] == "BHP"
    assert env["EMAIL_DELIVERY"] == "unavailable"
    assert "SES_REGION" not in env and "EMAIL_SENDER_ADDRESS" not in env
    # No approved model yet: the safe producer, no Bedrock settings.
    assert env["CONVERSATION_PRODUCER"] == "unavailable" and "BEDROCK_MODEL_ID" not in env
    assert "STATIC_DIR" not in env  # set in the image


def test_web_task_with_an_approved_model():
    s = stacks(
        bedrock_model_id="au.example-model-v1:0",
        bedrock_resource_arns=BEDROCK_ARNS,
        app_name="Canvas",
        header_text="Head",
        footer_text="Foot",
    )
    _, c = container(s.service, "plan-test-web")
    env = env_of(c)
    assert env["CONVERSATION_PRODUCER"] == "interviewer_bedrock"
    assert env["BEDROCK_MODEL_ID"] == "au.example-model-v1:0"
    assert env["BEDROCK_REGION"] == "ap-southeast-2" and env["BEDROCK_MAX_TOKENS"] == "1024"
    assert (env["APP_NAME"], env["HEADER_TEXT"], env["FOOTER_TEXT"]) == ("Canvas", "Head", "Foot")


def test_image_comes_from_the_foundation_repository_at_the_given_tag(test_env):
    for family in ("plan-test-web", "plan-test-sweeper"):
        _, c = container(test_env.service, family)
        image = json.dumps(c["Image"])
        assert image.endswith(':abc123"]]}') and "ExportsOutputRefRepository" in image


# --- sweeper -----------------------------------------------------------------------------------


def test_sweeper_task_and_schedule(test_env):
    t = test_env.service
    task, c = container(t, "plan-test-sweeper")
    assert c["Command"] == ["python", "-m", "app.expiry"]
    env = env_of(c)
    assert env == {
        "APP_ENV": "deployed",
        "AWS_REGION": "ap-southeast-2",
        "SESSIONS_TABLE_NAME": env["SESSIONS_TABLE_NAME"],
        "ACCESS_CODE_HASH_SECRET_ID": "plan-test/access-code-hash",
        "COOKIE_SIGNING_KEY_SECRET_ID": "plan-test/cookie-signing-key",
        "CONVERSATION_PRODUCER": "unavailable",
    }
    assert "PortMappings" not in c
    t.resource_count_is("AWS::Scheduler::Schedule", 1)
    sched = next(iter(resources(t, "AWS::Scheduler::Schedule").values()))["Properties"]
    assert sched["ScheduleExpression"] == "rate(5 minutes)"
    assert sched["FlexibleTimeWindow"] == {"Mode": "OFF"}
    target = sched["Target"]
    ecs = target["EcsParameters"]
    assert ecs["LaunchType"] == "FARGATE" and ecs["TaskCount"] == 1
    vpc = ecs["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert vpc["AssignPublicIp"] == "DISABLED"
    assert "TaskSg" in json.dumps(vpc["SecurityGroups"])
    assert all("private" in json.dumps(s) for s in as_list(vpc["Subnets"]))
    sweeper_td = [
        k
        for k, v in resources(t, "AWS::ECS::TaskDefinition").items()
        if v["Properties"]["Family"] == "plan-test-sweeper"
    ]
    assert ecs["TaskDefinitionArn"] == {"Ref": sweeper_td[0]}


def test_scheduler_role_can_only_run_the_sweeper_and_pass_its_roles(test_env):
    t = test_env.service
    statements = role_statements(t, "plan-test-scheduler")
    assert {a for s in statements for a in as_list(s["Action"])} == {"ecs:RunTask", "iam:PassRole"}
    run = next(s for s in statements if s["Action"] == "ecs:RunTask")
    assert "task-definition/plan-test-sweeper:*" in json.dumps(run["Resource"])
    assert "ecs:cluster" in json.dumps(run["Condition"])
    trust = next(
        r["Properties"]["AssumeRolePolicyDocument"]
        for r in resources(t, "AWS::IAM::Role").values()
        if r["Properties"].get("RoleName") == "plan-test-scheduler"
    )
    assert trust["Statement"][0]["Principal"] == {"Service": "scheduler.amazonaws.com"}


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
KMS = {"kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"}


def test_web_role_is_exactly_the_documented_permissions(test_env):
    t = test_env.service
    assert role_actions(t, "plan-test-web-task") == WEB_DDB | KMS | {
        "secretsmanager:GetSecretValue"
    }
    by_sid = {s["Sid"]: s for s in role_statements(t, "plan-test-web-task")}
    assert {a for a in as_list(by_sid["SessionsTable"]["Action"])} == WEB_DDB
    resources_ = json.dumps(by_sid["SessionsTable"]["Resource"])
    assert "/index/expiry-index" in resources_ and len(by_sid["SessionsTable"]["Resource"]) == 2
    secrets = by_sid["AccessSecrets"]["Resource"]
    assert len(secrets) == 2 and "*" not in json.dumps(secrets)
    assert all(s["Resource"] != "*" for s in by_sid.values())


def test_web_role_bedrock_only_on_the_given_arns_and_ses_not_by_default(test_env):
    assert not any(
        a.startswith(("bedrock:", "ses:"))
        for a in role_actions(test_env.service, "plan-test-web-task")
    )
    s = stacks(bedrock_model_id="au.example-model-v1:0", bedrock_resource_arns=BEDROCK_ARNS)
    stmt = next(
        x
        for x in role_statements(s.service, "plan-test-web-task")
        if "bedrock:InvokeModelWithResponseStream" in as_list(x["Action"])
    )
    assert as_list(stmt["Action"]) == ["bedrock:InvokeModelWithResponseStream"]  # never InvokeModel
    assert stmt["Resource"] == BEDROCK_ARNS.split(",")
    assert not any(a.startswith("ses:") for a in role_actions(s.service, "plan-test-web-task"))


def test_web_role_ses_only_when_enabled_and_conditioned_on_the_sender():
    s = stacks(
        enable_email="true",
        ses_identity_arn="arn:aws:ses:ap-southeast-2:123456789012:identity/plan.example.com",
        email_sender_address="plan@example.com",
        email_allowed_domains="example.com, bhp.com",
    )
    stmt = next(
        x
        for x in role_statements(s.service, "plan-test-web-task")
        if as_list(x["Action"]) == ["ses:SendEmail", "ses:SendRawEmail"]
    )
    assert stmt["Resource"] == "arn:aws:ses:ap-southeast-2:123456789012:identity/plan.example.com"
    assert stmt["Condition"] == {"StringEquals": {"ses:FromAddress": "plan@example.com"}}
    env = env_of(container(s.service, "plan-test-web")[1])
    assert env["EMAIL_DELIVERY"] == "ses"
    assert env["EMAIL_SENDER_ADDRESS"] == "plan@example.com"
    assert env["EMAIL_ALLOWED_DOMAINS"] == "example.com,bhp.com"
    assert env["SES_REGION"] == "ap-southeast-2"
    # The sweeper never gets SES.
    assert not any(a.startswith("ses:") for a in role_actions(s.service, "plan-test-sweeper-task"))


def test_email_without_egress_is_flagged_at_synth():
    app = build_app(
        {
            **BASE,
            "enable_email": "true",
            "ses_identity_arn": "arn:aws:ses:ap-southeast-2:123456789012:identity/x",
            "email_sender_address": "plan@example.com",
            "email_allowed_domains": "example.com",
        }
    )
    warnings = Annotations.from_stack(app.node.find_child("plan-test-service")).find_warning(
        "*", Match.string_like_regexp("no route to the SES API")
    )
    assert warnings


def test_sweeper_role_is_exactly_the_documented_permissions(test_env):
    t = test_env.service
    actions = role_actions(t, "plan-test-sweeper-task")
    assert (
        actions
        == {
            "dynamodb:Query",
            "dynamodb:DeleteItem",
            "dynamodb:BatchWriteItem",
            "dynamodb:GetItem",
        }
        | KMS
    )
    assert not any(a.startswith(("secretsmanager:", "bedrock:", "ses:")) for a in actions)
    by_sid = {s["Sid"]: s for s in role_statements(t, "plan-test-sweeper-task")}
    assert len(by_sid["SessionsTable"]["Resource"]) == 2


def test_sweeper_role_has_no_bedrock_even_when_the_web_role_does():
    s = stacks(bedrock_model_id="au.example-model-v1:0", bedrock_resource_arns=BEDROCK_ARNS)
    assert not any(
        a.startswith("bedrock:") for a in role_actions(s.service, "plan-test-sweeper-task")
    )


def test_execution_role_pulls_and_logs_only(test_env):
    t = test_env.service
    statements = role_statements(t, "plan-test-execution")
    assert {a for s in statements for a in as_list(s["Action"])} == {
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:GetAuthorizationToken",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
    }
    wildcard = [s for s in statements if s["Resource"] == "*"]
    assert [s["Action"] for s in wildcard] == ["ecr:GetAuthorizationToken"]


def test_task_roles_are_assumed_by_ecs_tasks_in_this_account_only(test_env):
    for r in resources(test_env.service, "AWS::IAM::Role").values():
        if r["Properties"]["RoleName"] == "plan-test-scheduler":
            continue
        st = r["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
        assert st["Principal"] == {"Service": "ecs-tasks.amazonaws.com"}
        assert "aws:SourceAccount" in json.dumps(st["Condition"])


def test_no_wildcard_actions_anywhere(test_env):
    for r in resources(test_env.service, "AWS::IAM::Policy").values():
        for st in r["Properties"]["PolicyDocument"]["Statement"]:
            assert not any(a == "*" or a.endswith(":*") for a in as_list(st["Action"]))


# --- observability -----------------------------------------------------------------------------


def test_alarms_notify_the_topic(test_env):
    t = test_env.service
    alarms = {
        v["Properties"]["AlarmName"]: v["Properties"]
        for v in resources(t, "AWS::CloudWatch::Alarm").values()
    }
    assert set(alarms) == {
        "plan-test-Target5xxRate",
        "plan-test-UnhealthyHosts",
        "plan-test-WebCpuHigh",
        "plan-test-WebMemoryHigh",
    }
    assert alarms["plan-test-UnhealthyHosts"]["MetricName"] == "UnHealthyHostCount"
    assert alarms["plan-test-UnhealthyHosts"]["Threshold"] == 0
    assert alarms["plan-test-WebCpuHigh"]["Threshold"] == 85
    assert alarms["plan-test-WebCpuHigh"]["MetricName"] == "CPUUtilization"
    assert alarms["plan-test-WebMemoryHigh"]["MetricName"] == "MemoryUtilization"
    assert alarms["plan-test-WebMemoryHigh"]["Threshold"] == 85
    assert "Metrics" in alarms["plan-test-Target5xxRate"]
    assert "HTTPCode_Target_5XX_Count" in json.dumps(alarms["plan-test-Target5xxRate"])
    for a in alarms.values():
        assert a["AlarmActions"] and a["TreatMissingData"] == "notBreaching"


def test_sweeper_failure_rule_targets_the_topic(test_env):
    t = test_env.service
    rule = next(iter(resources(t, "AWS::Events::Rule").values()))["Properties"]
    pattern = rule["EventPattern"]
    assert pattern["source"] == ["aws.ecs"]
    assert pattern["detail-type"] == ["ECS Task State Change"]
    detail = pattern["detail"]
    assert detail["lastStatus"] == ["STOPPED"]
    assert detail["group"] == ["family:plan-test-sweeper"]
    assert {"containers": {"exitCode": [{"anything-but": 0}]}} in detail["$or"]
    assert len(rule["Targets"]) == 1 and "AlarmTopic" in json.dumps(rule["Targets"][0]["Arn"])


def test_alarm_email_subscription_and_budget():
    s = stacks(alarm_email="ops@example.com", monthly_budget_usd="300")
    s.service.has_resource_properties(
        "AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "ops@example.com"}
    )
    budget = next(iter(resources(s.service, "AWS::Budgets::Budget").values()))["Properties"]
    assert budget["Budget"]["BudgetLimit"] == {"Amount": 300, "Unit": "USD"}
    assert budget["Budget"]["TimeUnit"] == "MONTHLY" and budget["Budget"]["BudgetType"] == "COST"
    kinds = {n["Notification"]["NotificationType"] for n in budget["NotificationsWithSubscribers"]}
    assert kinds == {"ACTUAL", "FORECASTED"}
    for n in budget["NotificationsWithSubscribers"]:
        assert n["Notification"]["Threshold"] == 80
        assert n["Notification"]["ThresholdType"] == "PERCENTAGE"
        assert n["Subscribers"] == [{"SubscriptionType": "EMAIL", "Address": "ops@example.com"}]


def test_default_budget_is_450_and_needs_an_email(prod_env, test_env):
    budget = next(iter(resources(prod_env.service, "AWS::Budgets::Budget").values()))["Properties"]
    assert budget["Budget"]["BudgetLimit"]["Amount"] == 450
    test_env.service.resource_count_is("AWS::Budgets::Budget", 0)
    test_env.service.resource_count_is("AWS::SNS::Subscription", 0)


def test_no_cloudtrail_resources(test_env):
    for t in (test_env.foundation, test_env.service):
        t.resource_count_is("AWS::CloudTrail::Trail", 0)


def test_outputs_for_the_smoke_test(test_env):
    outputs = j(test_env.service)["Outputs"]
    assert {"AlbDnsName", "ServiceUrl"} <= set(outputs)
    assert json.dumps(outputs["ServiceUrl"]).count("https://") == 1


# --- image tag ---------------------------------------------------------------------------------


def test_service_stack_requires_an_image_tag():
    app = build_app({k: v for k, v in BASE.items() if k != "image_tag"})
    from config import Config

    cfg = Config.from_context(app.node.try_get_context)
    foundation = app.node.find_child("plan-test-foundation")
    assert isinstance(foundation, FoundationStack)
    with pytest.raises(ConfigError, match="image_tag"):
        ServiceStack(app, "another-service", config=cfg, foundation=foundation)


def test_missing_image_tag_blocks_only_the_service_stack():
    app = build_app({k: v for k, v in BASE.items() if k != "image_tag"})
    foundation = app.node.find_child("plan-test-foundation")
    assert not Annotations.from_stack(foundation).find_error("*", Match.any_value())
    service = app.node.find_child("plan-test-service")
    # Not a ServiceStack: the CLI turns this error annotation into a failed synth.
    assert not isinstance(service, ServiceStack)
    assert Annotations.from_stack(service).find_error("*", Match.string_like_regexp("image_tag"))


def test_prod_uses_prod_names(prod_env):
    t = prod_env.service
    names = {r["Properties"]["RoleName"] for r in resources(t, "AWS::IAM::Role").values()}
    assert names == {
        "plan-prod-execution",
        "plan-prod-web-task",
        "plan-prod-sweeper-task",
        "plan-prod-scheduler",
    }
    _, c = container(t, "plan-prod-web")
    assert env_of(c)["ACCESS_CODE_HASH_SECRET_ID"] == "plan-prod/access-code-hash"
    assert CERT in json.dumps(j(t))
