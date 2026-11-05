"""GitHub Actions trust: an OIDC provider and one deploy role per environment.

Deployed once per account, by an administrator, by hand (`cdk deploy plan-github-oidc`). The
workflows then assume `plan-github-deploy-<env>` with short-lived tokens; no AWS keys are stored.

The role only starts deployments: it may assume the CDK bootstrap roles (which do the real work,
with whatever permissions the bootstrap gave them), push to its own ECR repository and read its
own stacks. It is not an administrator.
"""

from aws_cdk import ArnFormat, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

from config import OidcConfig

PROVIDER_HOST = "token.actions.githubusercontent.com"
BOOTSTRAP_ROLE_KINDS = (
    "deploy-role",
    "file-publishing-role",
    "image-publishing-role",
    "lookup-role",
)
ECR_PUSH_PULL = [
    "ecr:BatchCheckLayerAvailability",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer",
    "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart",
    "ecr:CompleteLayerUpload",
    "ecr:PutImage",
    "ecr:DescribeImages",
    "ecr:DescribeImageScanFindings",
]


class GithubOidcStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, config: OidcConfig, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if config.provider_arn:
            provider_arn = config.provider_arn
        else:
            provider_arn = iam.OidcProviderNative(
                self,
                "GithubProvider",
                url=f"https://{PROVIDER_HOST}",
                client_ids=["sts.amazonaws.com"],
            ).oidc_provider_arn

        self.roles: dict[str, iam.Role] = {}
        for env in config.environments:
            self.roles[env] = self._deploy_role(env, provider_arn, config)

    def _deploy_role(self, env: str, provider_arn: str, config: OidcConfig) -> iam.Role:
        role = iam.Role(
            self,
            f"Deploy{env.capitalize()}",
            role_name=f"plan-github-deploy-{env}",
            description=f"GitHub Actions deploys of the {env} environment",
            max_session_duration=None,
            assumed_by=iam.WebIdentityPrincipal(
                provider_arn,
                conditions={
                    "StringEquals": {
                        f"{PROVIDER_HOST}:aud": "sts.amazonaws.com",
                        # The GitHub Environment, so prod's required reviewers are a real gate.
                        f"{PROVIDER_HOST}:sub": f"repo:{config.github_repo}:environment:{env}",
                    }
                },
            ),
        )

        q = config.cdk_qualifier
        role.add_to_policy(
            iam.PolicyStatement(
                sid="AssumeCdkBootstrapRoles",
                actions=["sts:AssumeRole"],
                resources=[
                    self.format_arn(
                        service="iam",
                        region="",
                        resource="role",
                        resource_name=f"cdk-{q}-{kind}-{self.account}-{config.region}",
                    )
                    for kind in BOOTSTRAP_ROLE_KINDS
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrLogin", actions=["ecr:GetAuthorizationToken"], resources=["*"]
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrPushPullOwnRepository",
                actions=ECR_PUSH_PULL,
                resources=[
                    self.format_arn(
                        service="ecr",
                        region=config.region,
                        resource="repository",
                        resource_name=f"plan-{env}",
                    )
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadOwnStacks",
                actions=["cloudformation:DescribeStacks"],
                resources=[
                    self.format_arn(
                        service="cloudformation",
                        region=config.region,
                        resource="stack",
                        resource_name=f"plan-{env}-{suffix}/*",
                    )
                    for suffix in ("foundation", "service")
                ],
            )
        )
        # A destroyed secret otherwise sits in a 30-day recovery window that blocks reusing its
        # name. The destroy workflow purges the two it just deleted.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PurgeOwnDeletedSecrets",
                actions=["secretsmanager:DeleteSecret"],
                resources=[
                    self.format_arn(
                        service="secretsmanager",
                        region=config.region,
                        resource="secret",
                        resource_name=f"plan-{env}/*",
                        arn_format=ArnFormat.COLON_RESOURCE_NAME,
                    )
                ],
            )
        )

        # The Parameter Store backend's parameters are made by scripts/set-access-secrets.sh, not
        # by CloudFormation, so the destroy workflow deletes them. Delete only, only these.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="DeleteOwnParameters",
                actions=["ssm:DeleteParameter", "ssm:DeleteParameters"],
                resources=[
                    self.format_arn(
                        service="ssm",
                        region=config.region,
                        resource="parameter",
                        resource_name=f"plan-{env}/*",
                    )
                ],
            )
        )

        # Safety net: a Lambda-backed custom resource would leave a log group no stack owns. The
        # stacks currently have none.
        def log_group(name: str) -> str:
            return self.format_arn(
                service="logs",
                region=config.region,
                resource="log-group",
                resource_name=name,
                arn_format=ArnFormat.COLON_RESOURCE_NAME,
            )

        role.add_to_policy(
            iam.PolicyStatement(
                sid="FindLambdaLogGroups",
                actions=["logs:DescribeLogGroups"],
                resources=[log_group("*")],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PurgeOwnLambdaLogGroups",
                actions=["logs:DeleteLogGroup"],
                resources=[log_group(f"/aws/lambda/plan-{env}-*")],
            )
        )
        # Lets the destroy workflow prove nothing is left. Read-only, and the API has no
        # resource-level scoping.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="FindTaggedResources", actions=["tag:GetResources"], resources=["*"]
            )
        )
        return role
