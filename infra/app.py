#!/usr/bin/env python3
"""CDK entry point. Context keys and defaults are documented in config.py."""

import json
import os
from pathlib import Path

import aws_cdk as cdk

from config import Config, OidcConfig
from stacks.foundation import FoundationStack
from stacks.github_oidc import GithubOidcStack
from stacks.service import ServiceStack

TAGS = {"Project": "plan-on-a-page"}
# The organisation requires these tags on everything created here (its policies refuse creation
# without them). One file, read here and by scripts/set-access-secrets.sh.
ORG_TAGS: dict[str, str] = json.loads((Path(__file__).parent / "org-tags.json").read_text())


def build_app(context: dict[str, str] | None = None) -> cdk.App:
    app = cdk.App(context=context)
    account = os.environ.get("CDK_DEFAULT_ACCOUNT")  # set by the CLI from the credentials in use

    oidc = OidcConfig.from_context(app.node.try_get_context)
    GithubOidcStack(
        app,
        "plan-github-oidc",
        config=oidc,
        env=cdk.Environment(account=account, region=oidc.region),
    )

    if app.node.try_get_context("env"):
        config = Config.from_context(app.node.try_get_context)
        env = cdk.Environment(account=account, region=config.region)
        foundation = FoundationStack(app, f"{config.prefix}-foundation", config=config, env=env)
        service_name = f"{config.prefix}-service"
        if config.image_tag:
            ServiceStack(app, service_name, config=config, foundation=foundation, env=env)
        else:
            # Foundation must be deployable before any image exists, but the service must never
            # be: an error on this stack stops `cdk deploy <env>-service` with a clear message.
            missing = cdk.Stack(app, service_name, env=env)
            cdk.Annotations.of(missing).add_error(
                "context 'image_tag' is required for the service stack "
                "(pass -c image_tag=<tag already pushed to the ECR repository>)"
            )
        cdk.Tags.of(app).add("Environment", config.env_name)
    cdk.Tags.of(app).add("Project", TAGS["Project"])
    for key, value in ORG_TAGS.items():
        cdk.Tags.of(app).add(key, value)
    return app


if __name__ == "__main__":
    build_app().synth()
