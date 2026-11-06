"""Every CDK context key the app reads, with defaults and validation, in one place.

Pass values with `-c key=value` (scripts/cdk-run.sh does this from environment variables, which
the deploy workflow fills from GitHub Environment variables). An empty value counts as "not
set". Nothing here is an account id or an ARN default: those always come from the caller.

key                       used by        default                  meaning
------------------------  -------------  -----------------------  ---------------------------------
env                       foundation,    (none)                   `test` or `prod`. Without it only
                          service                                 the OIDC stack exists.
profile                   foundation,    minimal for test,        `full` (the whole design) or
                          service        full for prod            `minimal` (cheap shared-account
                                                                  test: public task IPs, no NAT,
                                                                  endpoints, WAF, KMS key, alarms
                                                                  or autoscaling). minimal with
                                                                  env=prod is an error.
secrets_backend           foundation,    parameterstore for       Where the access code hash and
                          service        minimal, secretsmanager  cookie key live: `secretsmanager`
                                         for full                 (created by CloudFormation) or
                                                                  `parameterstore` (SSM SecureString
                                                                  parameters, created by
                                                                  scripts/set-access-secrets.sh,
                                                                  for accounts that deny
                                                                  secretsmanager:CreateSecret).
region                    all            ap-southeast-2           Deployment region.
availability_zones        service        <region>a,<region>b      Exactly two zones. Explicit, so
                                                                  synth needs no AWS lookup.
vpc_cidr                  service        10.20.0.0/16             VPC range (ignored with vpc_id).
vpc_id                    service        none (create a VPC)      minimal only: use this existing
                                                                  VPC instead of creating one (for
                                                                  an account at its VPC / internet
                                                                  gateway quota). Needs
                                                                  public_subnet_ids.
public_subnet_ids         service        none                     With vpc_id: exactly two PUBLIC
                                                                  subnets (route to an internet
                                                                  gateway) in the two
                                                                  availability_zones, in that order.
certificate_arn           service        required                 Existing ACM certificate, in
                                                                  `region`, for the ALB.
domain_name               service        none                     Public name (smoke test, alias).
hosted_zone_id            service        none                     With domain_name: create the
                                                                  Route 53 alias record.
image_tag                 service        required                 ECR tag to run (the git SHA).
web_desired_count         service        2 (minimal: 1)           Web tasks, 1 to 10. Full: scales
                                                                  from this to max(4, this).
                                                                  Minimal: no autoscaling.
bedrock_model_id          service        none                     Approved model or profile id.
                                                                  Empty: producer `unavailable`
                                                                  and no Bedrock permissions.
bedrock_resource_arns     service        none                     Comma-separated ARNs the web role
                                                                  may invoke. Needed with the id.
bedrock_role_arn          service        none                     Role in another account the web
                                                                  role assumes to call Bedrock
                                                                  (minimal profile only). The web
                                                                  role then has no Bedrock actions,
                                                                  so no resource ARNs are needed.
                                                                  Must match backend/aws-config.
bedrock_log_error_text    service        false                    Log the short text of Bedrock's
                                                                  validation errors. Test only: it
                                                                  can quote request content.
bedrock_region            service        region                   Must equal region.
app_name                  service        Plan on a Page           On-screen name, 1 to 80 chars.
header_text               service        Plan on a Page           Text beside the logo.
footer_text               service        BHP                      Footer text.
alarm_email               service        none (prod: required)    Alarm and budget recipient.
                                                                  Minimal: alarms, topic and budget
                                                                  exist only when it is set.
monthly_budget_usd        service        450                      Budget; alerts at 80% actual and
                                                                  forecast. Needs alarm_email.
rate_limit_sensitive      service        100                      WAF requests per IP per 5 min on
                                                                  /api/access, /api/sessions.
rate_limit_general        service        2000                     WAF requests per IP per 5 min.
enable_email              service        false                    Send email through SES. Needs
                                                                  egress this design lacks
                                                                  (docs/deployment.md).
ses_identity_arn          with email     none                     SES identity the role may use.
email_sender_address      with email     none                     Sender, also the FromAddress
                                                                  condition.
email_allowed_domains     with email     none                     Comma-separated domains.
ses_region                with email     region                   SES region.
github_repo               oidc           BlueSky-Digital-Labs/    `owner/name` allowed to assume
                                         bhp-plan-on-page-        the deploy roles.
                                         generator
github_oidc_provider_arn  oidc           none                     Reuse an existing GitHub OIDC
                                                                  provider instead of creating one.
cdk_qualifier             oidc           hnb659fds                CDK bootstrap qualifier.
github_environments       oidc           test,prod                Environments that get a role.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ENVIRONMENTS = ("test", "prod")
PROFILES = ("full", "minimal")
SECRETS_BACKENDS = ("secretsmanager", "parameterstore")
DEFAULT_REGION = "ap-southeast-2"
DEFAULT_GITHUB_REPO = "BlueSky-Digital-Labs/bhp-plan-on-page-generator"
MAX_BRANDING_CHARS = 80  # same limit as backend/app/config.py

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")
_VPC_ID = re.compile(r"^vpc-[0-9a-f]{8,17}$")
_SUBNET_ID = re.compile(r"^subnet-[0-9a-f]{8,17}$")
_ACM_ARN = re.compile(r"^arn:aws[a-z-]*:acm:(?P<region>[a-z0-9-]+):\d{12}:certificate/[\w-]+$")
_IAM_ROLE_ARN = re.compile(r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]+$")
_EMAIL = re.compile(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$")


class ConfigError(ValueError):
    """A context value is missing or invalid. Messages name keys, never secrets."""


Getter = Callable[[str], Any]


def _text(get: Getter, key: str, default: str | None = None) -> str | None:
    value = get(key)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _required(get: Getter, key: str) -> str:
    value = _text(get, key)
    if value is None:
        raise ConfigError(f"context '{key}' is required (pass -c {key}=...)")
    return value


def _int(get: Getter, key: str, default: int, low: int, high: int) -> int:
    raw = _text(get, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"context '{key}' must be a whole number") from None
    if not low <= value <= high:
        raise ConfigError(f"context '{key}' must be between {low} and {high}")
    return value


def _bool(get: Getter, key: str) -> bool:
    raw = get(key)
    if raw is None or str(raw).strip() == "":
        return False
    value = str(raw).strip().lower()
    if value not in ("true", "false"):
        raise ConfigError(f"context '{key}' must be true or false")
    return value == "true"


def _list(get: Getter, key: str) -> tuple[str, ...]:
    raw = _text(get, key) or ""
    return tuple(dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()))


def _branding(get: Getter, key: str, default: str) -> str:
    value = re.sub(r"\s+", " ", _text(get, key, default) or default)
    if len(value) > MAX_BRANDING_CHARS or _CONTROL.search(value):
        raise ConfigError(f"context '{key}' must be 1 to {MAX_BRANDING_CHARS} plain characters")
    return value


@dataclass(frozen=True)
class Config:
    """Settings for the foundation and service stacks of one environment."""

    env_name: str
    profile: str
    secrets_backend: str
    region: str
    availability_zones: tuple[str, str]
    vpc_cidr: str
    vpc_id: str | None
    public_subnet_ids: tuple[str, str] | None
    certificate_arn: str
    domain_name: str | None
    hosted_zone_id: str | None
    image_tag: str | None
    web_desired_count: int
    bedrock_model_id: str | None
    bedrock_resource_arns: tuple[str, ...]
    bedrock_role_arn: str | None
    bedrock_log_error_text: bool
    bedrock_region: str
    app_name: str
    header_text: str
    footer_text: str
    alarm_email: str | None
    monthly_budget_usd: int
    rate_limit_sensitive: int
    rate_limit_general: int
    enable_email: bool
    ses_identity_arn: str | None
    email_sender_address: str | None
    email_allowed_domains: tuple[str, ...]
    ses_region: str

    @property
    def minimal(self) -> bool:
        return self.profile == "minimal"

    @property
    def parameter_store(self) -> bool:
        return self.secrets_backend == "parameterstore"

    @property
    def access_code_id(self) -> str:
        """What ACCESS_CODE_HASH_SECRET_ID holds: a parameter name (leading slash) or a secret."""
        return (
            f"/{self.prefix}/access-code-hash"
            if self.parameter_store
            else (f"{self.prefix}/access-code-hash")
        )

    @property
    def cookie_key_id(self) -> str:
        return (
            f"/{self.prefix}/cookie-signing-key"
            if self.parameter_store
            else (f"{self.prefix}/cookie-signing-key")
        )

    @property
    def prefix(self) -> str:
        return f"plan-{self.env_name}"

    @property
    def conversation_producer(self) -> str:
        # Without an approved model the app is deployed in its safe "never fabricates" mode.
        return "interviewer_bedrock" if self.bedrock_model_id else "unavailable"

    def require_image_tag(self) -> str:
        if not self.image_tag:
            raise ConfigError(
                "context 'image_tag' is required for the service stack "
                "(pass -c image_tag=<tag already pushed to the plan-<env> ECR repository>)"
            )
        return self.image_tag

    @classmethod
    def from_context(cls, get: Getter) -> "Config":
        env_name = _required(get, "env")
        if env_name not in ENVIRONMENTS:
            raise ConfigError(f"context 'env' must be one of: {', '.join(ENVIRONMENTS)}")
        region = _text(get, "region", DEFAULT_REGION) or DEFAULT_REGION

        profile = _text(get, "profile", "minimal" if env_name == "test" else "full")
        if profile not in PROFILES:
            raise ConfigError(f"context 'profile' must be one of: {', '.join(PROFILES)}")
        if profile == "minimal" and env_name == "prod":
            raise ConfigError("context 'profile=minimal' is for test only; prod must be full")

        backend = _text(
            get, "secrets_backend", "parameterstore" if profile == "minimal" else "secretsmanager"
        )
        if backend not in SECRETS_BACKENDS:
            raise ConfigError(
                f"context 'secrets_backend' must be one of: {', '.join(SECRETS_BACKENDS)}"
            )

        certificate_arn = _required(get, "certificate_arn")
        match = _ACM_ARN.match(certificate_arn)
        if not match:
            raise ConfigError("context 'certificate_arn' must be an ACM certificate ARN")
        if match["region"] != region:
            raise ConfigError(f"context 'certificate_arn' must be in region {region}")

        azs = _list(get, "availability_zones") or (f"{region}a", f"{region}b")
        if len(azs) != 2:
            raise ConfigError("context 'availability_zones' must list exactly two zones")

        vpc_id = _text(get, "vpc_id")
        subnet_ids = _list(get, "public_subnet_ids")
        if vpc_id or subnet_ids:
            if profile != "minimal":
                raise ConfigError("context 'vpc_id' is for the minimal profile only")
            if not _VPC_ID.match(vpc_id or ""):
                raise ConfigError("context 'vpc_id' must look like vpc-0123abcd")
            if len(subnet_ids) != 2 or not all(_SUBNET_ID.match(x) for x in subnet_ids):
                raise ConfigError(
                    "context 'public_subnet_ids' must list exactly two subnet ids "
                    "(subnet-0123abcd), one per availability zone"
                )
            if subnet_ids[0] == subnet_ids[1]:
                raise ConfigError("context 'public_subnet_ids' must name two different subnets")

        domain_name = _text(get, "domain_name")
        hosted_zone_id = _text(get, "hosted_zone_id")
        if hosted_zone_id and not domain_name:
            raise ConfigError("context 'hosted_zone_id' needs 'domain_name'")

        model_id = _text(get, "bedrock_model_id")
        bedrock_arns = _list(get, "bedrock_resource_arns")
        bedrock_role_arn = _text(get, "bedrock_role_arn")
        if bedrock_role_arn:
            if not _IAM_ROLE_ARN.match(bedrock_role_arn):
                raise ConfigError("context 'bedrock_role_arn' must be an IAM role ARN")
            if not model_id:
                raise ConfigError("context 'bedrock_role_arn' needs 'bedrock_model_id'")
            if profile != "minimal":
                # Assuming the role calls STS, and the full profile has no route to it.
                raise ConfigError("context 'bedrock_role_arn' is for the minimal profile only")
        elif model_id and not bedrock_arns:
            raise ConfigError("context 'bedrock_resource_arns' is required with 'bedrock_model_id'")
        if any(not a.startswith("arn:") for a in bedrock_arns):
            raise ConfigError("context 'bedrock_resource_arns' must be a list of ARNs")

        bedrock_region = _text(get, "bedrock_region", region) or region
        if model_id and bedrock_region != region:
            # The only Bedrock endpoint in this design is in the deployment region.
            raise ConfigError("context 'bedrock_region' must equal 'region' (no cross-region path)")

        alarm_email = _text(get, "alarm_email")
        if env_name == "prod" and not alarm_email:
            raise ConfigError("context 'alarm_email' is required for prod")
        if alarm_email and not _EMAIL.match(alarm_email):
            raise ConfigError("context 'alarm_email' must be an email address")

        enable_email = _bool(get, "enable_email")
        ses_identity_arn = _text(get, "ses_identity_arn")
        sender = _text(get, "email_sender_address")
        domains = _list(get, "email_allowed_domains")
        if enable_email:
            missing = [
                key
                for key, value in (
                    ("ses_identity_arn", ses_identity_arn),
                    ("email_sender_address", sender),
                    ("email_allowed_domains", domains),
                )
                if not value
            ]
            if missing:
                raise ConfigError(f"enable_email=true also needs: {', '.join(missing)}")
            if not _EMAIL.match(sender or ""):
                raise ConfigError("context 'email_sender_address' must be a plain email address")

        return cls(
            env_name=env_name,
            profile=profile,
            secrets_backend=backend,
            region=region,
            availability_zones=(azs[0], azs[1]),
            vpc_cidr=_text(get, "vpc_cidr", "10.20.0.0/16") or "10.20.0.0/16",
            vpc_id=vpc_id,
            public_subnet_ids=(subnet_ids[0], subnet_ids[1]) if vpc_id else None,
            certificate_arn=certificate_arn,
            domain_name=domain_name,
            hosted_zone_id=hosted_zone_id,
            image_tag=_text(get, "image_tag"),
            web_desired_count=_int(
                get, "web_desired_count", 1 if profile == "minimal" else 2, 1, 10
            ),
            bedrock_model_id=model_id,
            bedrock_resource_arns=bedrock_arns,
            bedrock_role_arn=bedrock_role_arn,
            bedrock_log_error_text=_bool(get, "bedrock_log_error_text"),
            bedrock_region=bedrock_region,
            app_name=_branding(get, "app_name", "Plan on a Page"),
            header_text=_branding(get, "header_text", "Plan on a Page"),
            footer_text=_branding(get, "footer_text", "BHP"),
            alarm_email=alarm_email,
            monthly_budget_usd=_int(get, "monthly_budget_usd", 450, 1, 1_000_000),
            rate_limit_sensitive=_int(get, "rate_limit_sensitive", 100, 10, 2_000_000),
            rate_limit_general=_int(get, "rate_limit_general", 2000, 10, 2_000_000),
            enable_email=enable_email,
            ses_identity_arn=ses_identity_arn,
            email_sender_address=sender,
            email_allowed_domains=domains,
            ses_region=_text(get, "ses_region", region) or region,
        )


@dataclass(frozen=True)
class OidcConfig:
    """Settings for the once-per-account GitHub OIDC stack."""

    region: str
    github_repo: str
    provider_arn: str | None
    cdk_qualifier: str
    environments: tuple[str, ...]

    @classmethod
    def from_context(cls, get: Getter) -> "OidcConfig":
        repo = _text(get, "github_repo", DEFAULT_GITHUB_REPO) or DEFAULT_GITHUB_REPO
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ConfigError("context 'github_repo' must look like owner/name")
        envs = _list(get, "github_environments") or ENVIRONMENTS
        unknown = [e for e in envs if e not in ENVIRONMENTS]
        if unknown:
            raise ConfigError(f"context 'github_environments' has unknown: {', '.join(unknown)}")
        qualifier = _text(get, "cdk_qualifier", "hnb659fds") or "hnb659fds"
        if not re.fullmatch(r"[A-Za-z0-9]{1,10}", qualifier):
            raise ConfigError("context 'cdk_qualifier' must be 1 to 10 letters or digits")
        return cls(
            region=_text(get, "region", DEFAULT_REGION) or DEFAULT_REGION,
            github_repo=repo,
            provider_arn=_text(get, "github_oidc_provider_arn"),
            cdk_qualifier=qualifier,
            environments=tuple(envs),
        )
