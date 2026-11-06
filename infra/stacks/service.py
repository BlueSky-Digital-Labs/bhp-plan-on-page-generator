"""Service stack: network, load balancer, WAF, the web service, the expiry sweeper, alarms.

Two profiles (config.py). `full`: Fargate tasks run in private isolated subnets (no NAT, no
internet route) and reach AWS only through the VPC endpoints below, so a service without an
endpoint here (SES, for one) is unreachable from the tasks. `minimal`, for a cheap test in a
shared account only: public subnets, tasks with public IPs, no endpoints, WAF, autoscaling or
(unless alarm_email is set) alarms. Same roles and actions, minus KMS.
"""

from configparser import ConfigParser
from pathlib import Path

from aws_cdk import Annotations, CfnOutput, Duration, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as event_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

from config import Config
from stacks.foundation import EXPIRY_INDEX, FoundationStack

APP_PORT = 8000
SWEEPER_SCHEDULE = "rate(15 minutes)"
SWEEPER_COMMAND = ["python", "-m", "app.expiry"]
WEB_ACTIONS_DDB = [
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:Query",
    "dynamodb:BatchWriteItem",
    "dynamodb:ConditionCheckItem",
]
SWEEPER_ACTIONS_DDB = [
    "dynamodb:Query",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:GetItem",
]
KMS_ACTIONS = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
# Streaming only: ConverseStream is all the app calls (docs/bedrock.md forbids plain InvokeModel).
BEDROCK_ACTIONS = ["bedrock:InvokeModelWithResponseStream"]
# With bedrock_role_arn the web role calls Bedrock as a role in another account, through this
# profile in the config file baked into the image (backend/aws-config).
BEDROCK_PROFILE = "rnd-bedrock"
AWS_CONFIG_FILE = "/app/aws-config"
BEDROCK_PROFILE_FILE = Path(__file__).resolve().parents[2] / "backend" / "aws-config"
FIVE_MINUTES = 300


class ServiceStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        foundation: FoundationStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config
        self.foundation = foundation
        self.prefix = config.prefix
        image_tag = config.require_image_tag()  # fail at synth, before anything is deployed
        if config.bedrock_role_arn:
            self._check_bedrock_profile(config.bedrock_role_arn)
        if config.enable_email and config.email_log_error_text:
            Annotations.of(self).add_warning(
                "email_log_error_text=true: AWS's text for a denied send goes to CloudWatch Logs "
                "and can include an address. Test only; unset it afterwards."
            )
        if config.bedrock_model_id and config.bedrock_log_error_text:
            Annotations.of(self).add_warning(
                "bedrock_log_error_text=true: the text of Bedrock's validation errors goes to "
                "CloudWatch Logs and can quote request content. Test only; unset it afterwards."
            )
        if config.enable_email and not config.minimal:
            Annotations.of(self).add_warning(
                "enable_email=true, but the tasks have no route to the SES API (private isolated "
                "subnets, no SES endpoint, no NAT). Sending will fail until that is resolved. "
                "See docs/deployment.md, open decisions."
            )

        self.task_subnets = ec2.SubnetSelection(
            subnet_type=(
                ec2.SubnetType.PUBLIC if config.minimal else ec2.SubnetType.PRIVATE_ISOLATED
            )
        )
        self._network()
        self._roles()
        self._web_service(image_tag)
        self._edge()
        if not config.minimal:
            self._waf()
        self._sweeper(image_tag)
        self._alarms()
        self._outputs()

    # --- network -------------------------------------------------------------------------------

    def _network(self) -> None:
        cfg = self.config
        if cfg.vpc_id and cfg.public_subnet_ids:
            # An account at its VPC / internet-gateway quota: use a VPC that already exists. No
            # lookup (so a synth needs no AWS call): the two public subnets are named. Only our own
            # security groups, load balancer and tasks are added to it, and only those are removed
            # when this stack is destroyed.
            self.vpc = ec2.Vpc.from_vpc_attributes(
                self,
                "Vpc",
                vpc_id=cfg.vpc_id,
                availability_zones=list(cfg.availability_zones),
                public_subnet_ids=list(cfg.public_subnet_ids),
            )
        else:
            self.vpc = self._new_vpc()
        private = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)
        self._security_groups(private)

    def _new_vpc(self) -> ec2.Vpc:
        cfg = self.config
        return ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(cfg.vpc_cidr),
            availability_zones=list(cfg.availability_zones),
            nat_gateways=0,
            subnet_configuration=(
                [
                    # Tasks live here too, and get their public IP from the service setting.
                    ec2.SubnetConfiguration(
                        name="public",
                        subnet_type=ec2.SubnetType.PUBLIC,
                        cidr_mask=24,
                        map_public_ip_on_launch=False,
                    )
                ]
                if cfg.minimal
                else [
                    ec2.SubnetConfiguration(
                        name="public",
                        subnet_type=ec2.SubnetType.PUBLIC,
                        cidr_mask=24,
                        map_public_ip_on_launch=False,  # only the ALB lives here
                    ),
                    ec2.SubnetConfiguration(
                        name="private", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24
                    ),
                ]
            ),
        )

    def _security_groups(self, private: ec2.SubnetSelection) -> None:
        cfg = self.config
        self.alb_sg = ec2.SecurityGroup(
            self, "AlbSg", vpc=self.vpc, description="Public ALB", allow_all_outbound=False
        )
        self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")
        self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP, redirected")

        if cfg.minimal:
            # Public IPs, so the tasks reach the AWS APIs over the internet. The only way in is
            # from the ALB (added when the service is attached to the target group).
            self.task_sg = ec2.SecurityGroup(
                self, "TaskSg", vpc=self.vpc, description="Fargate tasks", allow_all_outbound=False
            )
            self.task_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "AWS APIs")
            self.task_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.udp(53), "DNS")
            self.task_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(53), "DNS over TCP")
            return

        # Egress is 443 only. The gateway endpoints (S3, DynamoDB) are addressed by prefix list,
        # which a synth cannot resolve, so the destination is open; the isolated subnets have no
        # route anywhere except the endpoints, which is what actually bounds it.
        self.task_sg = ec2.SecurityGroup(
            self, "TaskSg", vpc=self.vpc, description="Fargate tasks", allow_all_outbound=False
        )
        self.task_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "VPC endpoints")

        self.endpoint_sg = ec2.SecurityGroup(
            self,
            "EndpointSg",
            vpc=self.vpc,
            description="Interface endpoints",
            allow_all_outbound=False,
        )
        self.endpoint_sg.add_ingress_rule(self.task_sg, ec2.Port.tcp(443), "From tasks only")

        interface_services = {
            "BedrockRuntime": ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
            # The app reads its two secrets from one backend; only that one needs a path.
            **(
                {"Ssm": ec2.InterfaceVpcEndpointAwsService.SSM}
                if cfg.parameter_store
                else {"SecretsManager": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER}
            ),
            "Logs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
            "EcrDocker": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
        }
        for name, service in interface_services.items():
            self.vpc.add_interface_endpoint(
                name,
                service=service,
                subnets=private,
                security_groups=[self.endpoint_sg],
                private_dns_enabled=True,
                open=False,  # the default would admit the whole VPC CIDR
            )
        self.vpc.add_gateway_endpoint(
            "DynamoDb", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB, subnets=[private]
        )
        # ECR serves image layers from S3.
        self.vpc.add_gateway_endpoint(
            "S3", service=ec2.GatewayVpcEndpointAwsService.S3, subnets=[private]
        )

    # --- roles ---------------------------------------------------------------------------------

    def _roles(self) -> None:
        cfg = self.config
        f = self.foundation
        table_arns = [f.table.table_arn, f"{f.table.table_arn}/index/{EXPIRY_INDEX}"]
        tasks = iam.ServicePrincipal(
            "ecs-tasks.amazonaws.com",
            conditions={"StringEquals": {"aws:SourceAccount": self.account}},
        )

        # Pull and log only. The ECR and log statements are added by the image and log driver.
        self.execution_role = iam.Role(
            self, "ExecutionRole", role_name=f"{self.prefix}-execution", assumed_by=tasks
        )

        self.web_role = iam.Role(
            self, "WebTaskRole", role_name=f"{self.prefix}-web-task", assumed_by=tasks
        )
        self.web_role.add_to_policy(
            iam.PolicyStatement(sid="SessionsTable", actions=WEB_ACTIONS_DDB, resources=table_arns)
        )
        if f.key:
            self.web_role.add_to_policy(
                iam.PolicyStatement(sid="Cmk", actions=KMS_ACTIONS, resources=[f.key.key_arn])
            )
        if cfg.parameter_store:
            # Exactly two parameters, one action. A SecureString under the AWS-managed key needs
            # no statement; one under the environment's key is covered by the Cmk statement.
            self.web_role.add_to_policy(
                iam.PolicyStatement(
                    sid="AccessParameters",
                    actions=["ssm:GetParameter"],
                    resources=[
                        self.format_arn(
                            service="ssm",
                            resource="parameter",
                            resource_name=f"{self.prefix}/{name}",
                        )
                        for name in ("access-code-hash", "cookie-signing-key")
                    ],
                )
            )
        else:
            self.web_role.add_to_policy(
                iam.PolicyStatement(
                    sid="AccessSecrets",
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[f.access_code_secret.secret_arn, f.cookie_key_secret.secret_arn],
                )
            )
        if cfg.bedrock_role_arn:
            self.web_role.add_to_policy(
                iam.PolicyStatement(
                    sid="AssumeBedrockRole",
                    actions=["sts:AssumeRole"],
                    resources=[cfg.bedrock_role_arn],
                )
            )
        elif cfg.bedrock_model_id:
            self.web_role.add_to_policy(
                iam.PolicyStatement(
                    sid="InvokeApprovedModel",
                    actions=BEDROCK_ACTIONS,
                    resources=list(cfg.bedrock_resource_arns),
                )
            )
        if cfg.enable_email:
            self.web_role.add_to_policy(
                iam.PolicyStatement(
                    sid="SendFromOneAddress",
                    actions=["ses:SendEmail"],
                    resources=[cfg.ses_identity_arn],
                    conditions={"StringEquals": {"ses:FromAddress": cfg.email_sender_address}},
                )
            )

        # Deletes expired sessions. Never reads the secrets, never calls Bedrock or SES.
        self.sweeper_role = iam.Role(
            self, "SweeperTaskRole", role_name=f"{self.prefix}-sweeper-task", assumed_by=tasks
        )
        self.sweeper_role.add_to_policy(
            iam.PolicyStatement(
                sid="SessionsTable", actions=SWEEPER_ACTIONS_DDB, resources=table_arns
            )
        )
        if f.key:
            self.sweeper_role.add_to_policy(
                iam.PolicyStatement(sid="Cmk", actions=KMS_ACTIONS, resources=[f.key.key_arn])
            )

    # --- containers ----------------------------------------------------------------------------

    def _access_env(self) -> dict[str, str]:
        # Identifiers only. The app reads the secret values itself through its task role.
        env = {
            "APP_ENV": "deployed",
            "AWS_REGION": self.config.region,
            "SESSIONS_TABLE_NAME": self.foundation.table.table_name,
            # Secret names, or (parameter store) parameter names with a leading slash.
            "ACCESS_CODE_HASH_SECRET_ID": self.config.access_code_id,
            "COOKIE_SIGNING_KEY_SECRET_ID": self.config.cookie_key_id,
        }
        if self.config.parameter_store:
            env["SECRETS_BACKEND"] = "parameterstore"  # Secrets Manager is the app's default
        return env

    @staticmethod
    def _check_bedrock_profile(role_arn: str) -> None:
        """The image's profile and the permission to assume must name the same role."""
        parser = ConfigParser()
        parser.read(BEDROCK_PROFILE_FILE)
        baked = parser.get(f"profile {BEDROCK_PROFILE}", "role_arn", fallback=None)
        if baked != role_arn:
            raise ValueError(
                f"bedrock_role_arn does not match role_arn in {BEDROCK_PROFILE_FILE.name} "
                f"({baked}); change both, then rebuild the image"
            )

    def _web_env(self) -> dict[str, str]:
        cfg = self.config
        env = {
            **self._access_env(),
            "CONVERSATION_PRODUCER": cfg.conversation_producer,
            "APP_NAME": cfg.app_name,
            "HEADER_TEXT": cfg.header_text,
            "FOOTER_TEXT": cfg.footer_text,
            "EMAIL_DELIVERY": "ses" if cfg.enable_email else "unavailable",
        }
        if cfg.bedrock_model_id:
            env["BEDROCK_REGION"] = cfg.bedrock_region
            env["BEDROCK_MODEL_ID"] = cfg.bedrock_model_id
            env["BEDROCK_MAX_TOKENS"] = "1024"
        if cfg.bedrock_model_id and cfg.bedrock_log_error_text:
            env["BEDROCK_LOG_ERROR_TEXT"] = "true"
        if cfg.bedrock_role_arn:
            env["AWS_CONFIG_FILE"] = AWS_CONFIG_FILE
            env["BEDROCK_PROFILE"] = BEDROCK_PROFILE
        if cfg.enable_email:
            env["EMAIL_SENDER_ADDRESS"] = cfg.email_sender_address or ""
            env["EMAIL_ALLOWED_DOMAINS"] = ",".join(cfg.email_allowed_domains)
            env["SES_REGION"] = cfg.ses_region
            if cfg.email_log_error_text:
                env["EMAIL_LOG_ERROR_TEXT"] = "true"
        return env

    def _task_definition(
        self,
        construct_id: str,
        *,
        family: str,
        cpu: int,
        memory: int,
        role: iam.Role,
        image_tag: str,
        log_group,
        stream_prefix: str,
        environment: dict[str, str],
        command: list[str] | None = None,
    ) -> ecs.FargateTaskDefinition:
        task_def = ecs.FargateTaskDefinition(
            self,
            construct_id,
            family=family,
            cpu=cpu,
            memory_limit_mib=memory,
            task_role=role,
            execution_role=self.execution_role,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=ecs.CpuArchitecture.X86_64,  # the workflow builds linux/amd64
            ),
        )
        task_def.add_volume(name="tmp")  # ephemeral, per task, gone when the task stops
        container = task_def.add_container(
            "app",
            image=ecs.ContainerImage.from_ecr_repository(self.foundation.repository, image_tag),
            command=command,
            environment=environment,
            readonly_root_filesystem=True,
            logging=ecs.LogDrivers.aws_logs(stream_prefix=stream_prefix, log_group=log_group),
        )
        container.add_mount_points(
            ecs.MountPoint(container_path="/tmp", source_volume="tmp", read_only=False)
        )
        return task_def

    def _web_service(self, image_tag: str) -> None:
        cfg = self.config
        self.web_task_def = self._task_definition(
            "WebTaskDef",
            family=f"{self.prefix}-web",
            cpu=512,
            memory=1024,
            role=self.web_role,
            image_tag=image_tag,
            log_group=self.foundation.web_log_group,
            stream_prefix="web",
            environment=self._web_env(),
        )
        self.web_task_def.default_container.add_port_mappings(
            ecs.PortMapping(container_port=APP_PORT)
        )

        self.cluster = ecs.Cluster(self, "Cluster", cluster_name=self.prefix, vpc=self.vpc)
        self.service = ecs.FargateService(
            self,
            "WebService",
            service_name=f"{self.prefix}-web",
            cluster=self.cluster,
            task_definition=self.web_task_def,
            desired_count=cfg.web_desired_count,
            security_groups=[self.task_sg],
            vpc_subnets=self.task_subnets,
            assign_public_ip=cfg.minimal,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            health_check_grace_period=Duration.seconds(60),
            min_healthy_percent=100,
            max_healthy_percent=200,
            availability_zone_rebalancing=(
                None if cfg.minimal else ecs.AvailabilityZoneRebalancing.ENABLED
            ),
        )
        if cfg.minimal:
            return  # one task, no autoscaling
        scaling = self.service.auto_scale_task_count(
            min_capacity=cfg.web_desired_count, max_capacity=max(4, cfg.web_desired_count)
        )
        scaling.scale_on_cpu_utilization(
            "Cpu",
            target_utilization_percent=60,
            scale_in_cooldown=Duration.minutes(5),
            scale_out_cooldown=Duration.minutes(1),
        )

    # --- load balancer, DNS, WAF ---------------------------------------------------------------

    def _edge(self) -> None:
        cfg = self.config
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=self.vpc,
            internet_facing=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=self.alb_sg,
            idle_timeout=Duration.seconds(120),  # replies stream
            drop_invalid_header_fields=True,
        )
        self.target_group = elbv2.ApplicationTargetGroup(
            self,
            "WebTargets",
            vpc=self.vpc,
            target_type=elbv2.TargetType.IP,
            protocol=elbv2.ApplicationProtocol.HTTP,
            port=APP_PORT,
            deregistration_delay=Duration.seconds(30),
            health_check=elbv2.HealthCheck(
                path="/api/health",
                healthy_http_codes="200",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
        )
        self.alb.add_listener(
            "Https",
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            certificates=[elbv2.ListenerCertificate.from_arn(cfg.certificate_arn)],
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,  # TLS 1.3 with a TLS 1.2 floor
            open=False,
            default_action=elbv2.ListenerAction.forward([self.target_group]),
        )
        self.alb.add_listener(
            "Http",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            open=False,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS", port="443", permanent=True
            ),
        )
        # Adds the ALB -> task security group rule on the app port.
        self.service.attach_to_application_target_group(self.target_group)

        if cfg.domain_name and cfg.hosted_zone_id:
            route53.CfnRecordSet(
                self,
                "Alias",
                hosted_zone_id=cfg.hosted_zone_id,
                name=cfg.domain_name,
                type="A",
                alias_target=route53.CfnRecordSet.AliasTargetProperty(
                    dns_name=self.alb.load_balancer_dns_name,
                    hosted_zone_id=self.alb.load_balancer_canonical_hosted_zone_id,
                    evaluate_target_health=True,
                ),
            )

    def _waf(self) -> None:
        cfg = self.config

        def visibility(name: str) -> wafv2.CfnWebACL.VisibilityConfigProperty:
            # No sampled requests: a sample records headers, which include the session cookie.
            return wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{self.prefix}-{name}",
                sampled_requests_enabled=False,
            )

        def managed(name: str, priority: int, overrides=()) -> wafv2.CfnWebACL.RuleProperty:
            return wafv2.CfnWebACL.RuleProperty(
                name=name,
                priority=priority,
                override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS",
                        name=name,
                        rule_action_overrides=[
                            wafv2.CfnWebACL.RuleActionOverrideProperty(
                                name=rule,
                                action_to_use=wafv2.CfnWebACL.RuleActionProperty(count={}),
                            )
                            for rule in overrides
                        ]
                        or None,
                    )
                ),
                visibility_config=visibility(name),
            )

        def path_match(path: str, constraint: str) -> wafv2.CfnWebACL.StatementProperty:
            return wafv2.CfnWebACL.StatementProperty(
                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                    search_string=path,
                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                    positional_constraint=constraint,
                    text_transformations=[
                        wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="NONE")
                    ],
                )
            )

        def rate(
            name: str, priority: int, limit: int, scope_down=None
        ) -> wafv2.CfnWebACL.RuleProperty:
            return wafv2.CfnWebACL.RuleProperty(
                name=name,
                priority=priority,
                action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                statement=wafv2.CfnWebACL.StatementProperty(
                    rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                        limit=limit,
                        aggregate_key_type="IP",
                        evaluation_window_sec=FIVE_MINUTES,
                        scope_down_statement=scope_down,
                    )
                ),
                visibility_config=visibility(name),
            )

        # /api/access is matched exactly so the page's cheap GET /api/access-state poll does not
        # count against the brute-force limit (users share an egress IP). /api/sessions has no
        # sibling path.
        sensitive = wafv2.CfnWebACL.StatementProperty(
            or_statement=wafv2.CfnWebACL.OrStatementProperty(
                statements=[
                    path_match("/api/access", "EXACTLY"),
                    path_match("/api/sessions", "STARTS_WITH"),
                ]
            )
        )
        self.web_acl = wafv2.CfnWebACL(
            self,
            "WebAcl",
            name=f"{self.prefix}-web",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=visibility("web-acl"),
            rules=[
                rate("RateLimitSensitivePaths", 1, cfg.rate_limit_sensitive, sensitive),
                rate("RateLimitGeneral", 2, cfg.rate_limit_general),
                # Uploads of up to 5 MB (docs/attachments.md) exceed this rule's 8 KB body limit.
                managed("AWSManagedRulesCommonRuleSet", 10, overrides=("SizeRestrictions_BODY",)),
                managed("AWSManagedRulesKnownBadInputsRuleSet", 20),
            ],
        )
        wafv2.CfnWebACLAssociation(
            self,
            "WebAclAssociation",
            resource_arn=self.alb.load_balancer_arn,
            web_acl_arn=self.web_acl.attr_arn,
        )

    # --- sweeper -------------------------------------------------------------------------------

    def _sweeper(self, image_tag: str) -> None:
        family = f"{self.prefix}-sweeper"
        self.sweeper_family = family
        self.sweeper_task_def = self._task_definition(
            "SweeperTaskDef",
            family=family,
            cpu=256,
            memory=512,
            role=self.sweeper_role,
            image_tag=image_tag,
            log_group=self.foundation.sweeper_log_group,
            stream_prefix="sweeper",
            environment={**self._access_env(), "CONVERSATION_PRODUCER": "unavailable"},
            command=SWEEPER_COMMAND,
        )

        role = iam.Role(
            self,
            "SchedulerRole",
            role_name=f"{self.prefix}-scheduler",
            assumed_by=iam.ServicePrincipal(
                "scheduler.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                # Any revision of the family, so a new image does not need a new policy.
                resources=[
                    self.format_arn(
                        service="ecs",
                        resource="task-definition",
                        resource_name=f"{family}:*",
                    )
                ],
                conditions={"ArnEquals": {"ecs:cluster": self.cluster.cluster_arn}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[self.sweeper_role.role_arn, self.execution_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
            )
        )

        self.sweeper_schedule = scheduler.CfnSchedule(
            self,
            "SweeperSchedule",
            name=f"{self.prefix}-sweeper",
            description="Delete sessions older than 24 hours (python -m app.expiry)",
            schedule_expression=SWEEPER_SCHEDULE,
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            state="ENABLED",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=self.cluster.cluster_arn,
                role_arn=role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_retry_attempts=2, maximum_event_age_in_seconds=600
                ),
                ecs_parameters=scheduler.CfnSchedule.EcsParametersProperty(
                    task_definition_arn=self.sweeper_task_def.task_definition_arn,
                    launch_type="FARGATE",
                    task_count=1,
                    network_configuration=scheduler.CfnSchedule.NetworkConfigurationProperty(
                        awsvpc_configuration=scheduler.CfnSchedule.AwsVpcConfigurationProperty(
                            subnets=self.vpc.select_subnets(
                                subnet_type=self.task_subnets.subnet_type
                            ).subnet_ids,
                            security_groups=[self.task_sg.security_group_id],
                            assign_public_ip="ENABLED" if self.config.minimal else "DISABLED",
                        )
                    ),
                ),
            ),
        )

    # --- alarms and budget ---------------------------------------------------------------------

    def _alarms(self) -> None:
        cfg = self.config
        if cfg.minimal and not cfg.alarm_email:
            return  # nobody to tell: no topic, alarms, rule or budget
        self.alarm_topic = sns.Topic(self, "AlarmTopic", topic_name=f"{self.prefix}-alarms")
        if cfg.alarm_email:
            self.alarm_topic.add_subscription(subscriptions.EmailSubscription(cfg.alarm_email))
        action = cw_actions.SnsAction(self.alarm_topic)
        five = Duration.minutes(5)

        def alarm(name: str, metric, threshold: float, description: str, periods: int = 2):
            created = cloudwatch.Alarm(
                self,
                name,
                alarm_name=f"{self.prefix}-{name}",
                alarm_description=description,
                metric=metric,
                threshold=threshold,
                evaluation_periods=periods,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            created.add_alarm_action(action)
            return created

        metrics = self.target_group.metrics
        errors = metrics.http_code_target(
            elbv2.HttpCodeTarget.TARGET_5XX_COUNT, statistic="Sum", period=five
        )
        requests = metrics.request_count(statistic="Sum", period=five)
        alarm(
            "Target5xxRate",
            cloudwatch.MathExpression(
                expression="IF(requests > 0, 100 * errors / requests, 0)",
                using_metrics={"errors": errors, "requests": requests},
                period=five,
            ),
            5,
            "More than 5% of requests answered 5xx by the app",
        )
        alarm(
            "UnhealthyHosts",
            metrics.unhealthy_host_count(statistic="Maximum", period=Duration.minutes(1)),
            0,
            "At least one web task is failing the ALB health check",
        )
        alarm(
            "WebCpuHigh",
            self.service.metric_cpu_utilization(statistic="Average", period=five),
            85,
            "Web service CPU above 85%",
        )
        alarm(
            "WebMemoryHigh",
            self.service.metric_memory_utilization(statistic="Average", period=five),
            85,
            "Web service memory above 85%",
        )

        # A stopped sweeper task with a non-zero exit code, or one that never started.
        self.sweeper_failed_rule = events.Rule(
            self,
            "SweeperTaskFailed",
            rule_name=f"{self.prefix}-sweeper-failed",
            description="Expiry sweeper task stopped with a failure",
            event_pattern=events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={
                    "clusterArn": [self.cluster.cluster_arn],
                    "group": [f"family:{self.sweeper_family}"],
                    "lastStatus": ["STOPPED"],
                    "$or": [
                        {"containers": {"exitCode": [{"anything-but": 0}]}},
                        {"stopCode": ["TaskFailedToStart"]},
                    ],
                },
            ),
            targets=[event_targets.SnsTopic(self.alarm_topic)],
        )

        if cfg.alarm_email:
            subscriber = budgets.CfnBudget.SubscriberProperty(
                subscription_type="EMAIL", address=cfg.alarm_email
            )

            def notification(kind: str) -> budgets.CfnBudget.NotificationWithSubscribersProperty:
                return budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type=kind,
                        comparison_operator="GREATER_THAN",
                        threshold=80,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[subscriber],
                )

            # Whole-account cost: test and prod share the account. Tag filters only work once the
            # tag is activated for cost allocation, and would fail silently if it is not.
            budgets.CfnBudget(
                self,
                "MonthlyBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=f"{self.prefix}-monthly",
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(
                        amount=cfg.monthly_budget_usd, unit="USD"
                    ),
                ),
                notifications_with_subscribers=[notification("ACTUAL"), notification("FORECASTED")],
            )

    # --- outputs -------------------------------------------------------------------------------

    def _outputs(self) -> None:
        cfg = self.config
        host = cfg.domain_name or self.alb.load_balancer_dns_name
        CfnOutput(self, "AlbDnsName", value=self.alb.load_balancer_dns_name)
        CfnOutput(self, "ServiceUrl", value=f"https://{host}")
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        CfnOutput(self, "WebServiceName", value=self.service.service_name)
        CfnOutput(self, "SweeperScheduleName", value=f"{self.prefix}-sweeper")
        # For running the sweeper by hand (scripts/canary-scan.md).
        CfnOutput(
            self,
            "TaskSubnetIds",
            value=",".join(
                self.vpc.select_subnets(subnet_type=self.task_subnets.subnet_type).subnet_ids
            ),
        )
        CfnOutput(self, "TaskAssignPublicIp", value="ENABLED" if cfg.minimal else "DISABLED")
        CfnOutput(self, "TaskSecurityGroupId", value=self.task_sg.security_group_id)
