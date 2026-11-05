"""Foundation stack: the data and registry resources the service stack builds on.

Created first so the workflow can push an image to ECR before the service stack exists.
Everything is disposable: removal policy DESTROY, so `cdk destroy` leaves nothing behind
(the KMS key stays in its 7-day pending-deletion window, the AWS minimum).
"""

from aws_cdk import ArnFormat, CfnOutput, Duration, RemovalPolicy, SecretValue, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from config import Config

EXPIRY_INDEX = "expiry-index"

# Not a valid Argon2 hash, so the app fails closed (503) until scripts/set-access-secrets.sh
# replaces it. It is not a secret. Do not edit it: a changed value would be written back over
# the real hash on the next deploy.
ACCESS_CODE_PLACEHOLDER = "NOT-SET-run-scripts/set-access-secrets.sh"


class FoundationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, config: Config, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        prefix = config.prefix

        # The minimal profile has no customer-managed key: AWS-owned encryption everywhere, and
        # no KMS permissions on any role.
        self.key: kms.Key | None = None
        if not config.minimal:
            self.key = kms.Key(
                self,
                "Key",
                alias=f"alias/{prefix}",
                description=f"{prefix} sessions table, secrets and logs",
                enable_key_rotation=True,
                pending_window=Duration.days(7),
                removal_policy=RemovalPolicy.DESTROY,
            )

            # CloudWatch Logs needs its own statement before it can write to a group this key
            # encrypts.
            self.key.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="CloudWatchLogs",
                    principals=[iam.ServicePrincipal(f"logs.{self.region}.amazonaws.com")],
                    actions=[
                        "kms:Encrypt*",
                        "kms:Decrypt*",
                        "kms:ReEncrypt*",
                        "kms:GenerateDataKey*",
                        "kms:Describe*",
                    ],
                    resources=["*"],
                    conditions={
                        "ArnLike": {
                            "kms:EncryptionContext:aws:logs:arn": self.format_arn(
                                service="logs",
                                resource="log-group",
                                resource_name=f"/plan/{config.env_name}/*",
                                arn_format=ArnFormat.COLON_RESOURCE_NAME,
                            )
                        }
                    },
                )
            )

        self.table = dynamodb.Table(
            self,
            "SessionsTable",
            table_name=f"{prefix}-sessions",
            partition_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=(
                dynamodb.TableEncryption.DEFAULT
                if config.minimal
                else dynamodb.TableEncryption.CUSTOMER_MANAGED
            ),
            encryption_key=self.key,
            # Sessions live 24 hours and are purged on request: nothing to restore.
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=False
            ),
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
        # Only CONV items set gsi_pk (always "SESSIONS"), so the sweeper can list sessions by
        # expiry without scanning. KEYS_ONLY: the index never holds conversation content.
        self.table.add_global_secondary_index(
            index_name=EXPIRY_INDEX,
            partition_key=dynamodb.Attribute(name="gsi_pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="expires_at", type=dynamodb.AttributeType.NUMBER),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )

        self.access_code_secret = secretsmanager.Secret(
            self,
            "AccessCodeHash",
            secret_name=f"{prefix}/access-code-hash",
            description="Argon2 hash of the access code. Set by scripts/set-access-secrets.sh.",
            secret_string_value=SecretValue.unsafe_plain_text(ACCESS_CODE_PLACEHOLDER),
            encryption_key=self.key,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.cookie_key_secret = secretsmanager.Secret(
            self,
            "CookieSigningKey",
            secret_name=f"{prefix}/cookie-signing-key",
            description="HMAC key for the session and grant cookies. Generated, never shown.",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=64,  # the app requires at least 32
                exclude_punctuation=True,
            ),
            encryption_key=self.key,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.repository = ecr.Repository(
            self,
            "Repository",
            repository_name=prefix,
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10, description="keep last 10")],
            empty_on_delete=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # In this stack so the CMK's key policy for CloudWatch Logs stays next to the key.
        self.web_log_group = self._log_group("WebLogs", f"/plan/{config.env_name}/web")
        self.sweeper_log_group = self._log_group("SweeperLogs", f"/plan/{config.env_name}/sweeper")

        # Names only. Secret values are never outputs.
        CfnOutput(self, "RepositoryName", value=self.repository.repository_name)
        CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
        CfnOutput(self, "SessionsTableName", value=self.table.table_name)
        CfnOutput(self, "AccessCodeSecretName", value=f"{prefix}/access-code-hash")
        CfnOutput(self, "CookieKeySecretName", value=f"{prefix}/cookie-signing-key")

    def _log_group(self, construct_id: str, name: str) -> logs.LogGroup:
        return logs.LogGroup(
            self,
            construct_id,
            log_group_name=name,
            retention=logs.RetentionDays.ONE_WEEK,
            encryption_key=self.key,
            removal_policy=RemovalPolicy.DESTROY,
        )
