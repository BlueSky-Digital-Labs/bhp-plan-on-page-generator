"""Minimal profile on a VPC that already exists, for an account at its VPC / gateway quota."""

import json

import pytest

from config import Config, ConfigError
from tests.conftest import BASE, MINIMAL, as_list, j, stacks
from tests.test_service import by_description, resources, rules

VPC = "vpc-0abc1234def567890"
SUBNETS = "subnet-0aaa1111bbb222233,subnet-0ccc4444ddd555566"
EXISTING = {"vpc_id": VPC, "public_subnet_ids": SUBNETS}


def get(ctx):
    return lambda key: ctx.get(key)


@pytest.fixture(scope="module")
def v():
    return stacks(profile="", **EXISTING)


# --- configuration -----------------------------------------------------------------------------


def test_the_option_is_read_and_defaults_to_creating_a_vpc():
    cfg = Config.from_context(get({**MINIMAL, **EXISTING}))
    assert cfg.vpc_id == VPC and cfg.public_subnet_ids == tuple(SUBNETS.split(","))
    plain = Config.from_context(get({**MINIMAL}))
    assert plain.vpc_id is None and plain.public_subnet_ids is None


@pytest.mark.parametrize(
    "ctx",
    [
        {"vpc_id": VPC},  # subnets missing
        {"public_subnet_ids": SUBNETS},  # vpc missing
        {"vpc_id": VPC, "public_subnet_ids": "subnet-0aaa1111bbb222233"},  # only one
        {"vpc_id": VPC, "public_subnet_ids": SUBNETS + ",subnet-0eee7777fff888899"},  # three
        {"vpc_id": VPC, "public_subnet_ids": "subnet-0aaa1111bbb222233,subnet-0aaa1111bbb222233"},
        {"vpc_id": "vpc-nope", "public_subnet_ids": SUBNETS},
        {"vpc_id": "0abc", "public_subnet_ids": SUBNETS},
        {"vpc_id": VPC, "public_subnet_ids": "subnet-0aaa1111bbb222233,not-a-subnet"},
        {"vpc_id": VPC + "; rm -rf /", "public_subnet_ids": SUBNETS},
    ],
)
def test_incomplete_or_malformed_ids_are_refused(ctx):
    with pytest.raises(ConfigError):
        Config.from_context(get({**MINIMAL, **ctx}))


def test_only_the_minimal_profile_may_use_an_existing_vpc():
    with pytest.raises(ConfigError, match="minimal"):
        Config.from_context(get({**BASE, "profile": "full", **EXISTING}))
    prod = {**BASE, "env": "prod", "alarm_email": "ops@example.com", **EXISTING}
    with pytest.raises(ConfigError):
        Config.from_context(get(prod))


# --- what is created ------------------------------------------------------------------------------


def test_no_vpc_subnet_gateway_or_route_is_created(v):
    types = {r["Type"] for t in (v.foundation, v.service) for r in j(t)["Resources"].values()}
    forbidden = {
        "AWS::EC2::VPC",
        "AWS::EC2::Subnet",
        "AWS::EC2::InternetGateway",
        "AWS::EC2::VPCGatewayAttachment",
        "AWS::EC2::RouteTable",
        "AWS::EC2::Route",
        "AWS::EC2::SubnetRouteTableAssociation",
        "AWS::EC2::NatGateway",
        "AWS::EC2::VPCEndpoint",
    }
    assert not types & forbidden


def test_only_our_own_security_groups_are_added_to_the_given_vpc(v):
    t = v.service
    groups = resources(t, "AWS::EC2::SecurityGroup")
    assert len(groups) == 2
    assert {json.dumps(g["Properties"]["VpcId"]) for g in groups.values()} == {json.dumps(VPC)}


def test_the_load_balancer_tasks_and_sweeper_use_exactly_the_given_subnets(v):
    t = v.service
    expected = SUBNETS.split(",")
    lb = next(iter(resources(t, "AWS::ElasticLoadBalancingV2::LoadBalancer").values()))
    assert sorted(lb["Properties"]["Subnets"]) == sorted(expected)

    service = next(iter(resources(t, "AWS::ECS::Service").values()))["Properties"]
    net = service["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert sorted(net["Subnets"]) == sorted(expected) and net["AssignPublicIp"] == "ENABLED"

    sched = next(iter(resources(t, "AWS::Scheduler::Schedule").values()))["Properties"]
    sweeper = sched["Target"]["EcsParameters"]["NetworkConfiguration"]["AwsvpcConfiguration"]
    assert sorted(as_list(sweeper["Subnets"])) == sorted(expected)

    target_group = next(iter(resources(t, "AWS::ElasticLoadBalancingV2::TargetGroup").values()))
    assert target_group["Properties"]["VpcId"] == VPC


def test_the_outputs_name_the_given_subnets(v):
    out = v.service.to_json()["Outputs"]
    assert sorted(out["TaskSubnetIds"]["Value"].split(",")) == sorted(SUBNETS.split(","))


def test_the_task_firewall_is_still_closed_except_to_the_alb(v):
    t = v.service
    alb_id, _ = by_description(t, "Public ALB")
    task_id, _ = by_description(t, "Fargate tasks")
    ingress = rules(t, task_id, "Ingress")
    assert [(r["FromPort"], r["SourceSecurityGroupId"]) for r in ingress] == [
        (8000, {"Fn::GetAtt": [alb_id, "GroupId"]})
    ]
    assert not any("CidrIp" in r for r in ingress)


def test_everything_else_is_the_same_as_a_minimal_deployment_that_creates_its_own_vpc(v):
    own = stacks(profile="")
    keep = lambda s: sorted(  # noqa: E731
        r["Type"]
        for t in (s.foundation, s.service)
        for r in j(t)["Resources"].values()
        if not r["Type"].startswith("AWS::EC2::") or r["Type"] == "AWS::EC2::SecurityGroup"
    )
    assert keep(v) == keep(own)  # same table, secrets, roles, ECS, ALB, schedule


def test_without_the_option_a_vpc_is_still_created():
    own = stacks(profile="")
    assert "AWS::EC2::VPC" in {r["Type"] for r in j(own.service)["Resources"].values()}
