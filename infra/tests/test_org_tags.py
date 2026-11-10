"""The organisation's required tags are on every stack and on every resource that can carry them."""

import json
import re
from pathlib import Path

import pytest

from tests.conftest import j, stacks

TAGS_FILE = Path(__file__).resolve().parents[1] / "org-tags.json"
REQUIRED = {
    "created_by": "nara@horizondigital.au",
    "approved_by": "basit@horizondigital.au",
    "project_name": "plan-on-a-page",
    "created_for": "Client-Prototype",
    "cost_center": "BHP-Prototype",
    "client": "BHP",
}
# Resource types in these stacks that accept tags, so each must carry all of them.
TAGGABLE = {
    "AWS::DynamoDB::Table",
    "AWS::ECR::Repository",
    "AWS::Logs::LogGroup",
    "AWS::KMS::Key",
    "AWS::SecretsManager::Secret",
    "AWS::EC2::SecurityGroup",
    "AWS::ECS::Cluster",
    "AWS::ECS::Service",
    "AWS::ECS::TaskDefinition",
    "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "AWS::ElasticLoadBalancingV2::TargetGroup",
    "AWS::IAM::Role",
    "AWS::SNS::Topic",
    "AWS::EC2::VPC",
    "AWS::EC2::Subnet",
    "AWS::EC2::InternetGateway",
    "AWS::EC2::RouteTable",
    "AWS::EC2::VPCEndpoint",
    "AWS::ElasticLoadBalancingV2::Listener",
    "AWS::WAFv2::WebACL",
    "AWS::CloudWatch::Alarm",
    "AWS::Events::Rule",
    "AWS::ApplicationAutoScaling::ScalableTarget",
    "AWS::IAM::OIDCProvider",
}
# Not in the list because CloudFormation gives these no Tags property: IAM policies, routes,
# security-group rules, listener rules' attachments, the KMS alias, the SNS topic policy, the WAF
# association and the EventBridge Scheduler schedule (a schedule cannot carry resource tags).
PROFILES = {
    "minimal": {"profile": ""},
    "minimal, existing VPC": {
        "profile": "",
        "vpc_id": "vpc-0abc1234def567890",
        "public_subnet_ids": "subnet-0aaa1111bbb222233,subnet-0ccc4444ddd555566",
    },
    "full": {"profile": "full"},
    "full, secrets manager": {"profile": "full", "secrets_backend": "secretsmanager"},
}


def test_every_type_that_can_carry_tags_is_checked():
    """If CloudFormation gives a resource a Tags property it is in the list above, so a new one
    cannot slip through untagged."""
    for ctx in PROFILES.values():
        s = stacks(**ctx)
        for template in (s.foundation, s.service, s.oidc):
            for resource in j(template)["Resources"].values():
                if "Tags" in resource.get("Properties", {}):
                    assert resource["Type"] in TAGGABLE, resource["Type"]


def tag_map(resource: dict) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in resource.get("Properties", {}).get("Tags", [])}


def test_the_tag_file_is_exactly_what_the_organisation_asked_for():
    assert json.loads(TAGS_FILE.read_text()) == REQUIRED


def test_the_tags_are_valid_for_aws():
    ok = re.compile(r"^[\w .:/=+\-@]*$")  # letters, digits, spaces and _ . : / = + - @
    for key, value in REQUIRED.items():
        assert ok.match(key) and ok.match(value) and 1 <= len(key) <= 128 and len(value) <= 256
        assert not key.lower().startswith("aws:")
    assert len(REQUIRED) + 2 <= 50  # the two tags already applied (Project, Environment)


@pytest.mark.parametrize("name", PROFILES)
def test_every_stack_carries_the_tags(name):
    s = stacks(**PROFILES[name])
    for stack in (s.app.node.find_child(f"plan-test-{kind}") for kind in ("foundation", "service")):
        assert {k: v for k, v in stack.tags.tag_values().items() if k in REQUIRED} == REQUIRED
    assert {k: v for k, v in s.app.node.find_child("plan-github-oidc").tags.tag_values().items()
            if k in REQUIRED} == REQUIRED  # fmt: skip


@pytest.mark.parametrize("name", PROFILES)
def test_every_taggable_resource_carries_the_tags(name):
    s = stacks(**PROFILES[name])
    checked = 0
    for template in (s.foundation, s.service):
        for logical_id, resource in j(template)["Resources"].items():
            if resource["Type"] in TAGGABLE:
                tags = tag_map(resource)
                assert {k: tags.get(k) for k in REQUIRED} == REQUIRED, (
                    resource["Type"],
                    logical_id,
                )
                checked += 1
    assert checked >= 8  # the table, repository, log groups, roles, firewalls, load balancer...


def test_the_dynamodb_table_and_ecr_repository_in_particular_are_tagged():
    s = stacks(profile="")
    table = next(
        r for r in j(s.foundation)["Resources"].values() if r["Type"] == "AWS::DynamoDB::Table"
    )
    repo = next(
        r for r in j(s.foundation)["Resources"].values() if r["Type"] == "AWS::ECR::Repository"
    )
    for resource in (table, repo):
        assert all(tag_map(resource).get(k) == v for k, v in REQUIRED.items())


def test_the_secrets_manager_secrets_are_tagged_when_that_backend_is_used():
    s = stacks(profile="full", secrets_backend="secretsmanager")
    secrets = [
        r
        for r in j(s.foundation)["Resources"].values()
        if r["Type"] == "AWS::SecretsManager::Secret"
    ]
    assert len(secrets) == 2
    for secret in secrets:
        assert all(tag_map(secret).get(k) == v for k, v in REQUIRED.items())


def test_the_two_existing_tags_are_still_applied():
    s = stacks(profile="")
    table = next(
        r for r in j(s.foundation)["Resources"].values() if r["Type"] == "AWS::DynamoDB::Table"
    )
    assert (
        tag_map(table).get("Project") == "plan-on-a-page"
        and tag_map(table).get("Environment") == "test"
    )
