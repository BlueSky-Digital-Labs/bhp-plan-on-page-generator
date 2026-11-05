"""Shared helpers: build the app for a given context and read templates as plain dicts."""

import json
from functools import cache

import pytest
from aws_cdk.assertions import Template

from app import build_app

CERT = "arn:aws:acm:ap-southeast-2:123456789012:certificate/11111111-2222-3333-4444-555555555555"
BEDROCK_ARNS = (
    "arn:aws:bedrock:ap-southeast-2:123456789012:inference-profile/au.example-model-v1:0,"
    "arn:aws:bedrock:ap-southeast-2::foundation-model/example.model-v1:0,"
    "arn:aws:bedrock:ap-southeast-4::foundation-model/example.model-v1:0"
)
# The existing tests describe the full design. env=test defaults to minimal, so say "full".
BASE = {
    "env": "test",
    "profile": "full",
    "secrets_backend": "secretsmanager",  # pinned: the minimal default is parameterstore
    "certificate_arn": CERT,
    "image_tag": "abc123",
}
MINIMAL = {k: v for k, v in BASE.items() if k != "profile"}  # env=test, so the default applies


class Stacks:
    def __init__(self, context: dict[str, str]):
        self.app = build_app(context)
        name = f"plan-{context['env']}" if "env" in context else None
        self.foundation = self._template(f"{name}-foundation") if name else None
        self.service = self._template(f"{name}-service") if name else None
        self.oidc = self._template("plan-github-oidc")
        self.service_stack = self.app.node.try_find_child(f"{name}-service") if name else None

    def _template(self, name: str) -> Template:
        return Template.from_stack(self.app.node.find_child(name))


@cache
def _build(items: tuple[tuple[str, str], ...]) -> Stacks:
    return Stacks(dict(items))


def stacks(**overrides: str) -> Stacks:
    return _build(tuple(sorted({**BASE, **overrides}.items())))


@pytest.fixture(scope="session")
def test_env() -> Stacks:
    return stacks()


@pytest.fixture(scope="session")
def prod_env() -> Stacks:
    return stacks(env="prod", alarm_email="ops@example.com")


def j(template: Template) -> dict:
    return json.loads(json.dumps(template.to_json()))


def as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def role_statements(template: Template, role_name: str) -> list[dict]:
    """Every statement from every policy attached to the role, plus a check nothing else is."""
    data = j(template)["Resources"]
    ids = [
        k
        for k, r in data.items()
        if r["Type"] == "AWS::IAM::Role" and r["Properties"].get("RoleName") == role_name
    ]
    assert len(ids) == 1, role_name
    role = data[ids[0]]["Properties"]
    assert "ManagedPolicyArns" not in role and "Policies" not in role
    out: list[dict] = []
    for r in data.values():
        if r["Type"] == "AWS::IAM::Policy" and {"Ref": ids[0]} in r["Properties"]["Roles"]:
            out += r["Properties"]["PolicyDocument"]["Statement"]
    return out


def role_actions(template: Template, role_name: str) -> set[str]:
    return {a for s in role_statements(template, role_name) for a in as_list(s["Action"])}
