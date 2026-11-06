# Deployment on AWS

How the app is built, deployed, rebuilt and rolled back. One runbook, written for the engineer
who has to do it. Code: `infra/` (CDK, Python), `.github/workflows/deploy.yml`, `destroy.yml`,
`scripts/`. Architecture: "AI Opportunity Canvas Assistant, Solution architecture on AWS v0.3".

> **Status: written and tested offline, never deployed.** Nothing here has been run against
> real AWS. The CDK tests, `cdk synth` and `cfn-lint` pass, the workflows pass `actionlint` and
> `shellcheck`, and the shell scripts were run against a stand-in `aws` and a local web server. Read [Not verified](#not-verified) before the first deploy
> and expect to fix things on it.

## Minimal test profile (shared account)

CDK context `profile` is `full` (the whole design below) or `minimal`. It defaults to `minimal`
for `test` and `full` for `prod`; `minimal` with `prod` is refused at synth. Set the GitHub
Environment variable `PROFILE` (or `-c profile=...`) to override it.

> **Minimal is for a throwaway test with synthetic data in a shared account. It is never for
> real BHP content, never for a release, and never for `prod`.**

**What it creates:** a VPC with two public subnets (2 AZs, an internet gateway, no NAT, no
endpoints); the ALB exactly as in full (HTTPS 443 with your certificate, HTTP 80 redirect, 120 s
idle timeout); **one** web task and the sweeper task, both in the public subnets with public IPs;
the ECR repository; the same DynamoDB table (same keys, `expiry-index` and TTL: a test checks it
against `backend/app/dynamo.py`); the two access secrets (in Parameter Store by default, see
[Secrets backend](#secrets-backend-secrets-manager-or-parameter-store)); two log groups (7 days); the ECS cluster and
service; the sweeper schedule (every 15 minutes); and the same four roles with the same
least-privilege actions minus KMS. Every removal policy is still `DESTROY`.

**What it skips, and what that costs you:**

| Skipped | Trade-off |
| --- | --- |
| Private subnets, endpoints | Tasks have public IPs, so AWS API traffic (DynamoDB, Secrets Manager, Bedrock, ECR, logs) goes over the internet, not a private path. Tasks accept inbound traffic only from the ALB security group (port 8000) and send out only on 443 and DNS, but they are on the public internet. |
| WAF | No managed rules and **no rate limit on the access code**: the shared code can be guessed by volume. Use a long code. |
| Customer-managed KMS key | DynamoDB uses AWS-owned encryption, the secrets and log groups AWS-managed or default encryption. Not a key you control or can audit. No `kms:` permission on any role. |
| Second task, autoscaling | A task restart is a short outage. Nothing scales. |
| Alarms, SNS, budget | Silence when something breaks. Set `ALARM_EMAIL` and they are created exactly as in full. |

Email through SES can work in minimal (the tasks have internet access), unlike in full.

**Rough cost (an estimate from memory of public prices, not from the AWS calculator):** about
US$45 to 65 a month if left running (one 0.5 vCPU / 1 GB task, the ALB, hourly public IPv4
addresses, small items), against roughly US$150 to 220 for full. Bedrock usage is extra. Destroy it
when not in use.

### What to ask the account owners for

Nothing in minimal needs WAF, KMS, VPC endpoint, SNS, CloudWatch alarm, Budgets, application
autoscaling or Route 53 permissions (those are **full-profile only**). The principal that creates
the resources (your user, or the CloudFormation execution role) needs these service families:

* **CloudFormation**: create, update, delete and describe stacks.
* **IAM**: create, delete and tag roles and put or delete their inline policies; `iam:PassRole`
  for the ECS tasks and the scheduler role. If the account has never used ECS or load balancers,
  `iam:CreateServiceLinkedRole` once for each.
* **EC2 (VPC)**: VPC, subnets, route tables, internet gateway, security groups and their rules,
  tags. No endpoint, NAT or Elastic IP actions.
* **Elastic Load Balancing v2**: load balancer, listeners, target group.
* **ECS**: cluster, task definitions, service.
* **EventBridge Scheduler**: create, update and delete a schedule.
* **DynamoDB**: create and delete a table with a global secondary index, enable TTL, tag.
* **ECR**: create and delete a repository and its lifecycle policy; and, for whoever pushes the
  image, login and push to `plan-test`.
* **Parameter Store** (the minimal default): for whoever runs `scripts/set-access-secrets.sh` and
  the Destroy workflow, `ssm:PutParameter`, `ssm:GetParameter` and `ssm:DeleteParameter(s)` on
  `parameter/plan-test/*`, plus `kms:Encrypt` on the key only if `KMS_KEY_ID` is set. No
  `secretsmanager:*` at all.
* **Secrets Manager** (only with `SECRETS_BACKEND=secretsmanager`): create, put value, tag and
  delete two secrets (`plan-test/*`). The shared account's SCPs deny `secretsmanager:CreateSecret`,
  which is why Parameter Store is the minimal default.
* **CloudWatch Logs**: create and delete two log groups, set retention, tag.
* **ACM**: `acm:ImportCertificate` (only for the self-signed certificate below).
* **CDK bootstrap**: if the account is already bootstrapped (qualifier `hnb659fds`), permission to
  assume the `cdk-hnb659fds-*` roles and to read the SSM parameter
  `/cdk-bootstrap/hnb659fds/version`. If not, someone with wider rights must run `cdk bootstrap`
  once (S3, ECR, IAM, SSM, CloudFormation).
* Quotas: one more VPC and internet gateway in the region (the default limit is 5 each). If the account
  is full, reuse an existing VPC instead ("Account already at its VPC quota", below).

You do not need the `plan-github-oidc` stack or GitHub Environments to use minimal: deploy by
hand as below. (The Deploy workflow works too if the account allows the OIDC provider and the two
deploy roles.)

### Required organisation tags

The organisation's policies refuse to create resources without a set of tags (the DynamoDB and Secrets
Manager denials were these policies). The tags live in one file, `infra/org-tags.json`:

| Tag | Value |
| --- | --- |
| `created_by` | `nara@horizondigital.au` |
| `approved_by` | `basit@horizondigital.au` |
| `project_name` | `plan-on-a-page` |
| `created_for` | `Client-Prototype` |
| `cost_center` | `BHP-Prototype` |
| `client` | `BHP` |

`infra/app.py` applies them to every stack and, through CloudFormation, to every resource that can carry
tags (the table, repository, log groups, roles, security groups, load balancer, VPC pieces, WAF and so on),
next to the existing `Project` and `Environment` tags. `infra/tests/test_org_tags.py` checks all of them in
every profile, and fails if a new taggable resource type appears that the list does not cover.
`scripts/set-access-secrets.sh` reads the same file and tags the Parameter Store parameters when it creates
them (AWS does not allow tags together with an overwrite, so an existing parameter is only overwritten).

To change a value, edit the file and redeploy. The EventBridge Scheduler schedule cannot carry tags at all
(CloudFormation gives it no tag property); if a policy demands tags on it, that is the one resource that
would still be refused.

### Account already at its VPC quota: use an existing VPC

A new account allows 5 VPCs and 5 internet gateways per region. If the shared account is full (check
with `aws ec2 describe-vpcs --query "length(Vpcs)"` and `describe-internet-gateways`), minimal can
use a VPC that already exists, so it creates none of its own: no VPC, subnets, internet gateway or
route tables. It adds only its own security groups, load balancer and tasks, and destroying the
stacks removes only those.

Pick a VPC that has two PUBLIC subnets (a route to an internet gateway) in two different zones. The
account's default VPC usually does:

```bash
aws ec2 describe-vpcs --query "Vpcs[].{id:VpcId,default:IsDefault,cidr:CidrBlock}" --output table
aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id> \
  --query "Subnets[].{id:SubnetId,az:AvailabilityZone,publicIpOnLaunch:MapPublicIpOnLaunch}" --output table
# a subnet is public if its route table has 0.0.0.0/0 -> igw-...
aws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc-id> \
  --query "RouteTables[].{id:RouteTableId,routes:Routes[].GatewayId,subnets:Associations[].SubnetId}" --output json
```

Then set (as environment variables for `scripts/cdk-run.sh`, or as GitHub Environment variables):

```bash
export VPC_ID=vpc-0123456789abcdef0
export PUBLIC_SUBNET_IDS=subnet-0aaa...,subnet-0bbb...      # exactly two, in two different zones
export AVAILABILITY_ZONES=ap-southeast-2a,ap-southeast-2b   # the zones of those two subnets, same order
```

Minimal only: `full` always creates its own VPC and refuses these settings. The subnets must really
be public, or the load balancer and the tasks (which need public IPs to reach the AWS APIs) will not
work. Other teams' resources in the same VPC are not touched, but they share its address space and
route tables, so check that nothing there blocks outbound 443.

### Signing in to AWS from GitHub: OIDC or access keys

The Deploy and Destroy workflows choose how to sign in with the Environment **variable** `AWS_AUTH`:

| `AWS_AUTH` | How | You need |
| --- | --- | --- |
| empty or `oidc` (default) | GitHub assumes the short-lived role `plan-github-deploy-<env>` | the `plan-github-oidc` stack deployed once (step 5 below) |
| `keys` | the stored access key | Environment **secrets** `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (and `AWS_SESSION_TOKEN` if the keys are temporary). No OIDC stack |

With either, the workflow then checks that the credentials belong to the account in `AWS_ACCOUNT_ID` and
stops if they do not. It also stops with a clear message if `AWS_AUTH=keys` and a secret is missing, or if
`AWS_AUTH` holds anything else.

If you use `keys`:

* Put them in the `test` **Environment** secrets (Settings, Environments, test), not in repository-wide
  secrets, and limit that Environment's deployment branches to the branches that should deploy (for example
  `main` and `feature/aws-setup`). Anyone who can change the workflow on an allowed branch can make it read
  the secrets, so keep that list short. GitHub masks the values in logs; the workflows never print them.
* Prefer a **dedicated IAM user** (or role) whose only job is this deployment, not a person's own keys.
  The identity must be able to do what the by-hand deploy does: assume the CDK bootstrap roles
  (`cdk-hnb659fds-*`), log in to ECR and push to `plan-<env>`, describe its two stacks, and purge its
  deleted secrets.
* Keys that came from an SSO login are **temporary** (hours). They need `AWS_SESSION_TOKEN` as well and must be
  refreshed in the secrets each time, so they suit a quick test, not a standing pipeline.
* Rotate or delete the keys when the test ends. A leaked long-lived key works until someone revokes it,
  which is the main reason OIDC is the better long-term choice.

### Certificate without a domain

The ALB needs an ACM certificate. With no domain, use a self-signed one:

```bash
AWS_PROFILE=SharedWL scripts/make-self-signed-cert.sh          # prints a certificate ARN
```

It generates a 2048-bit RSA key and a 365-day certificate with `openssl` in a private temporary
directory (deleted on exit, never printed), imports it with `aws acm import-certificate` into
`ap-southeast-2`, and prints the ARN: use that as `CERTIFICATE_ARN`. The ALB's DNS name is not
known before the first deploy, so the certificate carries a placeholder name; the browser will show
a certificate warning regardless, which is expected for a test. Click through it.

**Warning:** with an untrusted certificate, browsers may treat the page as not secure and refuse or
drop the `Secure` / `__Host-` cookies the app depends on, so entry could fail for a reason that
has nothing to do with the app. If so, the fallback is a real ACM certificate for a **subdomain**
you control (DNS validation is one CNAME record), and `DOMAIN_NAME` set to it. Delete the imported
certificate yourself when the test ends (`aws acm delete-certificate`); destroying the stacks does
not own it.

### Deploying by hand with your own profile

```bash
export AWS_PROFILE=SharedWL AWS_REGION=ap-southeast-2
export CERTIFICATE_ARN=<arn from the script or an ACM subdomain certificate>
# optional: ALARM_EMAIL, APP_NAME, BEDROCK_MODEL_ID + BEDROCK_RESOURCE_ARNS, ...
(cd infra && uv sync --frozen)

scripts/cdk-run.sh deploy test foundation --require-approval never

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export IMAGE_TAG=$(git rev-parse HEAD)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
docker build --platform linux/amd64 -t "$REGISTRY/plan-test:$IMAGE_TAG" .   # the tasks are x86_64
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"
docker push "$REGISTRY/plan-test:$IMAGE_TAG"

scripts/cdk-run.sh deploy test service --require-approval never
scripts/set-access-secrets.sh test        # creates the two SSM parameters (SecureString)
aws cloudformation describe-stacks --stack-name plan-test-service \
  --query "Stacks[0].Outputs[?OutputKey=='ServiceUrl' || OutputKey=='AlbDnsName']"
scripts/smoke-test.sh "https://<AlbDnsName>" --insecure
```

Build on Apple silicon without `--platform linux/amd64` and the tasks will fail with an exec
format error. To tear down: `IMAGE_TAG=destroy scripts/cdk-run.sh destroy test service --force`,
then the same for `foundation`, then
`aws ssm delete-parameters --names /plan-test/access-code-hash /plan-test/cookie-signing-key`
(the stacks do not own them; with `secretsmanager`, purge the two `plan-test/*` secrets instead,
see "Rebuild from scratch"), then delete the imported certificate.

## Secrets backend: Secrets Manager or Parameter Store

CDK context `secrets_backend` (GitHub variable `SECRETS_BACKEND`) chooses where the access code
hash and the cookie signing key live:

| | `secretsmanager` | `parameterstore` |
| --- | --- | --- |
| Default for | `full` | `minimal` |
| Names | `plan-<env>/access-code-hash`, `plan-<env>/cookie-signing-key` | `/plan-<env>/access-code-hash`, `/plan-<env>/cookie-signing-key` (leading slash) |
| Created by | CloudFormation (hash starts as a placeholder, key is generated) | `scripts/set-access-secrets.sh`; CloudFormation cannot create a SecureString, so the foundation stack has **no** secret or parameter resource |
| App setting | default | `SECRETS_BACKEND=parameterstore` on both containers; the two `*_SECRET_ID` variables hold the parameter names |
| Web role | `secretsmanager:GetSecretValue` on the two secrets | `ssm:GetParameter` on exactly the two parameter ARNs, nothing else |
| Removed by `cdk destroy` | yes (then purged by the Destroy workflow) | **no**: the Destroy workflow deletes the parameters |

Either can be used with either profile. Use `parameterstore` when the account denies Secrets
Manager: the shared test account's SCPs explicitly deny `secretsmanager:CreateSecret`
(CloudFormation failed creating both secrets), while `ssm put-parameter --type SecureString` works.

Trade-offs: the parameters are SecureStrings under the AWS-managed key `alias/aws/ssm` by default
(set `KMS_KEY_ID=alias/plan-<env>` when running the script to use the full profile's own key, which
the web role can already use). Standard parameters have no automatic rotation and no version
recovery window like Secrets Manager's. The app reads them at run time (not ECS secret
injection), so tasks start without them and the entry page answers `503` until they exist. The
cookie key is created by the script **once** (an existing one is left alone), so deleting the
parameters and rerunning the script ends all sessions. In the full profile the tasks reach SSM
through an `ssm` interface endpoint, which replaces the `secretsmanager` one.

## What gets created (full profile)

This section describes the `full` profile; minimal differs as above. Region `ap-southeast-2`. Two environments, `test` and `prod`, in one dedicated account. Each
environment is two stacks, `plan-<env>-foundation` and `plan-<env>-service`. A third stack,
`plan-github-oidc`, is deployed once per account by an administrator. Every resource is tagged
`Project=plan-on-a-page` and `Environment=<env>`, and every removal policy is `DESTROY`.

| Architecture box | CDK construct | Where |
| --- | --- | --- |
| Public DNS name | `route53.CfnRecordSet` alias (only with `DOMAIN_NAME` and `HOSTED_ZONE_ID`) | `service.py` `_edge` |
| ACM certificate | Not created. Imported by ARN (`ListenerCertificate.from_arn`) | `service.py` `_edge` |
| AWS WAF web ACL | `wafv2.CfnWebACL` (REGIONAL) + `CfnWebACLAssociation` | `service.py` `_waf` |
| Application Load Balancer, listeners | `ApplicationLoadBalancer`, HTTPS 443, HTTP 80 redirect, `ApplicationTargetGroup` | `service.py` `_edge` |
| VPC, public and private subnets | `ec2.Vpc` (2 AZs, no NAT, private subnets are `PRIVATE_ISOLATED`) | `service.py` `_network` |
| VPC endpoints | 5 interface (`bedrock-runtime`, `secretsmanager` or `ssm`, `logs`, `ecr.api`, `ecr.dkr`) and 2 gateway (`dynamodb`, `s3`) | `service.py` `_network` |
| Security groups | `AlbSg`, `TaskSg`, `EndpointSg` | `service.py` `_network` |
| ECS cluster, web service | `ecs.Cluster`, `FargateService` + autoscaling, `FargateTaskDefinition` | `service.py` `_web_service` |
| Expiry sweeper | `FargateTaskDefinition` (same image, command override) + `scheduler.CfnSchedule` + scheduler role | `service.py` `_sweeper` |
| Sessions table | `dynamodb.Table` + `expiry-index` GSI | `foundation.py` |
| Encryption key | `kms.Key` (rotation on, 7-day deletion window) | `foundation.py` |
| Secrets | `secretsmanager.Secret` x2 (backend `secretsmanager`), or nothing: two SSM parameters made by the script (backend `parameterstore`) | `foundation.py` |
| Image registry | `ecr.Repository` | `foundation.py` |
| Logs | `logs.LogGroup` x2 (7 days, encrypted with the key) | `foundation.py` |
| Roles | `WebTaskRole`, `SweeperTaskRole`, `ExecutionRole`, `SchedulerRole` | `service.py` `_roles`, `_sweeper` |
| Alarms | SNS topic, 4 `cloudwatch.Alarm`, EventBridge rule for a failed sweeper task | `service.py` `_alarms` |
| Cost budget | `budgets.CfnBudget` (needs `ALARM_EMAIL`) | `service.py` `_alarms` |
| GitHub deploy access | `OidcProviderNative` + role `plan-github-deploy-<env>` | `github_oidc.py` |

Not created: CloudTrail (account-level, out of scope), a NAT gateway, WAF logging, ALB access
logs, VPC flow logs.

Why two stacks: the workflow must push the image to ECR before the service stack can start
tasks, so ECR (and the table, secrets, key and log groups) live in `foundation`.

### Behaviour worth knowing

* The tasks are in subnets with **no route to the internet**. They reach AWS only through the
  endpoints above. Anything else the app calls will time out.
* **WAF**: the Common and Known-bad-inputs managed rule sets, a rate limit on the
  brute-force-sensitive paths (default 100 requests / 5 min / IP) and a general limit (default
  2000). Two deliberate differences from a literal reading of the design:
  * the sensitive limit matches `/api/access` **exactly** (not as a prefix) and `/api/sessions`
    as a prefix, so the page's cheap `GET /api/access-state` poll does not count against it
    (colleagues share an egress IP);
  * the Common rule `SizeRestrictions_BODY` is set to **Count**, because it blocks any request
    body over 8 KB and the app accepts 5 MB attachments ([attachments.md](attachments.md)).
  Sampled requests are off on every rule (a sample records headers, which include the session
  cookie). Turn them on briefly when tuning false positives.
* The web service runs with a read-only root filesystem and an ephemeral volume on `/tmp`.
* With no approved Bedrock model (`BEDROCK_MODEL_ID` empty) the app is deployed with
  `CONVERSATION_PRODUCER=unavailable` and **no Bedrock permissions**. It starts, serves the
  entry pages, and never fabricates a reply.

## Prerequisites (a human does these)

These are for the **full** profile (and for deploying through GitHub). For the minimal test
profile see [its permissions list](#what-to-ask-the-account-owners-for): WAF, KMS, VPC endpoint,
SNS, CloudWatch alarm, Budgets, autoscaling and Route 53 permissions are **full-profile only**.

1. **AWS account** dedicated to this app (full), and credentials for an administrator for the
   one-off steps below. The creating principal needs, beyond the minimal list: KMS (keys, aliases,
   key policies), WAFv2, EC2 VPC endpoints, SNS, CloudWatch alarms, EventBridge rules, Budgets,
   Application Auto Scaling and (with a domain) Route 53. With OIDC (the default) day-to-day deployments use no stored credentials; with `AWS_AUTH=keys` they use the stored access keys described below.
2. **CDK bootstrap**, once per account and region, with the default qualifier:
   ```bash
   cd infra && uv sync
   npx aws-cdk@2.1147.0 bootstrap aws://<account-id>/ap-southeast-2
   ```
   The default bootstrap gives CloudFormation an administrator role. That role, not the GitHub
   role, creates the resources. To narrow it, bootstrap with
   `--cloudformation-execution-policies <policy arn>`.
3. **ACM certificate in `ap-southeast-2`** for the public name, issued and validated (DNS
   validation). Note its ARN. It must be in the same region as the ALB.
4. **Bedrock** (skip until a model is approved, see the open decisions):
   * model access granted and quota raised for the chosen model in the account;
   * an **approved selection** in `backend/app/ai/policy.py` with its evidence, following
     [bedrock.md](bedrock.md). The app refuses to start with `interviewer_bedrock` otherwise,
     which would make every deployment fail its health check. Merge that change first. Two
     **test-only** entries are in the code now (Claude Sonnet 5.5 through `au.anthropic.claude-sonnet-5-5`
     and `openai.gpt-oss-120b-1:0`, both for the shared test account and marked "not release
     evidence"), so for that account only the variables below are needed, and switching between them is
     just `BEDROCK_MODEL_ID`. Include both models' ARNs in `BEDROCK_RESOURCE_ARNS` to switch without an
     IAM change;
   * the ARNs for `BEDROCK_RESOURCE_ARNS`: the Australian inference-profile ARN(s) plus the
     underlying foundation-model ARN in each destination region, from your evidence. For the two
     test-only models: `arn:aws:bedrock:ap-southeast-2:<account-id>:inference-profile/au.anthropic.claude-sonnet-5-5`,
     `arn:aws:bedrock:ap-southeast-2::foundation-model/anthropic.claude-sonnet-5-5`,
     `arn:aws:bedrock:ap-southeast-4::foundation-model/anthropic.claude-sonnet-5-5` and
     `arn:aws:bedrock:ap-southeast-2::foundation-model/openai.gpt-oss-120b-1:0`.
   * **Calling Bedrock through a role in another account** (when the deployment account itself is
     closed to the model): set `BEDROCK_ROLE_ARN` to that role, for example
     `arn:aws:iam::667736131634:role/bhp-plan-test-bedrock`, with `BEDROCK_MODEL_ID` as before.
     The web role (`plan-test-web-task`, the only name that role trusts) then gets
     `sts:AssumeRole` on that one ARN and **no Bedrock actions of its own**, so
     `BEDROCK_RESOURCE_ARNS` is not used. The container gets `AWS_CONFIG_FILE=/app/aws-config` and
     `BEDROCK_PROFILE=rnd-bedrock`; the profile is in `backend/aws-config`, baked into the image,
     and only the Bedrock client uses it. The ARN in that file and `BEDROCK_ROLE_ARN` must be the
     same: synthesis fails if they differ, so change both and redeploy (the image is rebuilt).
     Minimal profile only, because the full profile has no route to STS. Prompts and replies
     cross into the other account, which is a privacy decision for whoever approves the model.
   * **When a model call fails**, the `converse_stream_finished` line in `/plan/<env>/web` carries
     `failure_code`, `exception_type` (the AWS error code), `http_status`, `request_id` and
     `request_shape` (which parts the request had, never their content). To also log Bedrock's own
     short message for a `ValidationException`, set the GitHub variable `BEDROCK_LOG_ERROR_TEXT=true`
     and redeploy. That text can quote part of the request, so it is for the test deployment only:
     unset it afterwards and never use it with real data.
   * **When an email send is denied** (`email_send_failed`, `AccessDeniedException`), the app does
     not log AWS's reason. Set the GitHub variable `EMAIL_LOG_ERROR_TEXT=true` and redeploy to add
     it to that log line as `error_text` (the role, the action, the resource and whether a service
     control policy denied it). It can include an address, so it is for the test deployment only:
     unset it afterwards.
5. **Deploy the GitHub trust stack once** (administrator, by hand):
   ```bash
   cd infra && npx aws-cdk@2.1147.0 deploy plan-github-oidc
   # account already has the GitHub OIDC provider? reuse it:
   #   -c github_oidc_provider_arn=arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
   ```
   This creates `plan-github-deploy-test` and `plan-github-deploy-prod`. Each trusts only the
   GitHub Environment of the same name in `BlueSky-Digital-Labs/bhp-plan-on-page-generator`
   (the `sub` claim `repo:...:environment:<env>`) and the audience `sts.amazonaws.com`.
   They can: assume the CDK bootstrap roles, log in to ECR and push or pull only
   `plan-<env>`, describe their own two stacks, purge their own deleted secrets and Lambda log
   groups (a safety net: the stacks currently create no Lambda), and search by tag. Nothing else.
6. **Create the two GitHub Environments**, `test` and `prod` (Settings, Environments). For
   `prod`: required reviewers, and deployment branches limited to `main`. Set these as
   Environment **variables** (not secrets: none of them is secret):

   | Variable | Needed | Example / meaning |
   | --- | --- | --- |
   | `AWS_AUTH` | no | `oidc` (default) or `keys`: how the workflow signs in to AWS. See "Signing in to AWS from GitHub". With `keys`, add the Environment **secrets** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and, for temporary keys, `AWS_SESSION_TOKEN` |
   | `IMAGE_SCAN_FAIL_ON` | no | `CRITICAL` (default), `HIGH` or `none`: what the ECR image scan gate fails on |
   | `SECRETS_BACKEND` | no | `secretsmanager` or `parameterstore`. Empty: parameterstore for minimal, secretsmanager for full |
   | `PROFILE` | no | `full` or `minimal`. Empty: minimal for `test`, full for `prod`. Minimal with prod is refused |
   | `VPC_ID` | no | Minimal only: reuse this existing VPC instead of creating one (needs `PUBLIC_SUBNET_IDS`). For an account at its VPC quota |
   | `PUBLIC_SUBNET_IDS` | no | With `VPC_ID`: exactly two public subnets, comma separated |
   | `AVAILABILITY_ZONES` | no | The two zones, comma separated. With `VPC_ID`, the zones of those subnets in the same order. Default `<region>a,<region>b` |
   | `AWS_ACCOUNT_ID` | yes | 12 digits, used to build the role ARN |
   | `AWS_REGION` | no | `ap-southeast-2` (default) |
   | `CERTIFICATE_ARN` | yes | the ACM certificate from step 3 (minimal: the imported self-signed one) |
   | `DOMAIN_NAME` | no | `plan.example.com`. Used for the smoke test and the DNS alias |
   | `HOSTED_ZONE_ID` | no | with `DOMAIN_NAME`: creates the Route 53 alias. Without it, point DNS at the ALB yourself |
   | `BEDROCK_MODEL_ID` | no | the approved model or profile id. Empty = no AI, safe mode |
   | `BEDROCK_RESOURCE_ARNS` | with a model | comma-separated ARNs from step 4 |
   | `ALARM_EMAIL` | prod | alarm and budget recipient. In minimal, alarms and budget exist only if set |
   | `MONTHLY_BUDGET_USD` | no | default 450 |
   | `APP_NAME`, `HEADER_TEXT`, `FOOTER_TEXT` | no | defaults `Plan on a Page`, `Plan on a Page`, `BHP` ([branding.md](branding.md)) |
   | `WEB_DESIRED_COUNT` | no | default 2 (minimal: 1); 1 to 10. Full autoscales up to max(4, this); minimal does not |
   | `ENABLE_EMAIL`, `SES_IDENTITY_ARN`, `EMAIL_SENDER_ADDRESS`, `EMAIL_ALLOWED_DOMAINS` | no | leave off, see open decisions |
   | `BEDROCK_REGION`, `SES_REGION`, `RATE_LIMIT_SENSITIVE`, `RATE_LIMIT_GENERAL` | no | rarely needed |

   Every context key, its default and its validation are in `infra/config.py`. A bad value
   fails at synth with a message naming the key.
7. After the first deploy: **set the access code** (below).

## First deployment

1. Push to `main` (deploys `test`), or run **Actions, Deploy, Run workflow** and pick the
   environment. The workflow:
   1. runs the CI checks (`ci.yml`), skipped only for a rollback;
   2. builds the image (tagged with the full git SHA);
   3. signs in to AWS (`AWS_AUTH`: OIDC role or access keys) and checks the account matches;
   4. `cdk deploy plan-<env>-foundation`;
   5. pushes the image to `plan-<env>` (skipped if that tag already exists: tags are immutable), then
      **gates on ECR's own scan of it**: it fails on CRITICAL findings (`IMAGE_SCAN_FAIL_ON`: `CRITICAL`
      by default, `HIGH` to also fail on HIGH, `none` to switch it off);
   6. `cdk deploy plan-<env>-service -c image_tag=<sha>`;
   7. smoke test: `GET /api/health` until 200 (30 tries, 10 s apart), then a job summary with
      the image tag and URL. With no `HOSTED_ZONE_ID` it uses the ALB's own name and skips
      certificate verification for that single check.
2. **Set the access code.** The deploy leaves a placeholder that is not a valid hash, so entry
   answers `503` until this is done. With your own credentials:
   ```bash
   AWS_PROFILE=<profile> scripts/set-access-secrets.sh test
   ```
   It asks for the code twice (hidden), hashes it with Argon2 using the backend's own library
   and stores only the hash. The code is never echoed, never on a command line, never in a
   file. With `secretsmanager` it needs `secretsmanager:PutSecretValue` on the secret and
   `kms:GenerateDataKey` and `kms:Decrypt` on the environment's key. With `parameterstore` it writes
   the hash as a SecureString (overwriting), and creates `/plan-<env>/cookie-signing-key` only if
   it does not exist (a random 60+ character value, never printed); it needs `ssm:PutParameter`
   and `ssm:GetParameter` on `/plan-<env>/*`. The backend comes from `SECRETS_BACKEND` or the
   profile, as in `scripts/secrets-backend.sh`. No restart is needed.
3. **Confirm the emails.** SNS and AWS Budgets each send a confirmation to `ALARM_EMAIL`; alarms
   are silent until the SNS one is confirmed.
4. **DNS**, if you did not give a hosted zone: create a CNAME/alias from the name to the
   `AlbDnsName` stack output (`aws cloudformation describe-stacks --stack-name plan-<env>-service`).
5. Open the URL, enter the code, start a canvas.
6. Before relying on it, run the privacy check in [scripts/canary-scan.md](../scripts/canary-scan.md).

## Ordinary redeploy

Push to `main` (test) or run the Deploy workflow (either environment). Same steps as above. A
new SHA means a new task definition revision and a rolling deployment (min 100% healthy, max
200%). With the circuit breaker on, a deployment whose tasks keep failing rolls back by itself
and the workflow fails. Deployments to one environment queue; a running one is never cancelled.

`prod` deploys only from `main` (checked in the workflow) and waits for a reviewer.

## Rebuild from scratch

For when an environment should be wiped and recreated:

1. **Actions, Destroy**, pick the environment and type `destroy-<environment>` in `confirm`.
   It destroys the service, then the foundation, purges the two secrets (CloudFormation would
   otherwise leave them in a 30-day recovery window that blocks reusing the names), removes
   any Lambda log groups CDK helpers leave behind (a safety net; the stacks currently have none), and checks that both stacks and every
   tagged resource are gone. The KMS key stays for its 7-day pending-deletion window; it
   disappears by itself and is the one expected leftover.
2. **Actions, Deploy**, same environment.
3. **Set the access code again** (`scripts/set-access-secrets.sh`). The cookie key is new, so
   any old cookies are dead. (With `parameterstore` the Destroy workflow deleted the two
   parameters, so the script creates both afresh.)

All sessions and content are lost. That is the point of the design. The `plan-github-oidc`
stack is not touched; an administrator removes it with `cdk destroy plan-github-oidc`.

## Rollback

Run **Deploy** with `environment` and `image_tag` set to an earlier git SHA. It skips CI, the
build and the scan, checks the tag exists in ECR, and runs both CDK deployments with that
image. The infrastructure code comes from the ref you run the workflow on, so pick the ref that
matches the old image when the infrastructure changed too. ECR keeps the last 10 images, so
older ones cannot be rolled back to.

For an emergency without a rebuild: in the ECS console, update the service to the previous
task definition revision. The next deploy replaces it.

## Rotation

* **Access code** (June distributes it; an engineer applies it): follow
  [access-control.md](access-control.md#rotating-the-access-code-runbook), and use
  `scripts/set-access-secrets.sh <env>` for step 2 in place of the manual `put-secret-value`.
  It affects new entry only; running sessions continue until their 24 hour expiry.
* **Cookie signing key**: generates a new random key and stores it. **This ends every session
  and every pending grant at once** (each task notices within about a minute). Do it when a
  leak is suspected, or to end all sessions now.
  ```bash
  AWS_PROFILE=<profile> scripts/set-access-secrets.sh <env> --rotate-cookie-key
  ```
  It asks you to type a confirmation, then asks for the access code again (re-enter the current
  one) and replaces the key in whichever backend is in use. Without the flag an existing key is
  never touched. With `secretsmanager` you can also run
  `openssl rand -base64 48 | tr -d '\n=+/' | aws secretsmanager put-secret-value --secret-id plan-<env>/cookie-signing-key --secret-string file:///dev/stdin`.
  With `secretsmanager`, do not change the CDK definition of either secret afterwards without checking
  `aws secretsmanager get-secret-value` metadata first: see "Not verified".

## Where to look when it fails

| Symptom | Look at |
| --- | --- |
| Workflow fails before AWS | The job log. Missing variable: the "Check settings" step names it. Image scan gate: the step prints the counts; the findings are in ECR, `plan-<env>`, under the image tag. |
| `AccessDenied` assuming the role | `plan-github-oidc` deployed in this account? Environment name matches (`test`/`prod`)? Repo name in `github_repo`? |
| `cdk deploy` fails | CloudFormation console, stack **Events** tab: the first `FAILED` row. |
| Service deploy hangs, then rolls back | ECS console, service **Events** and a stopped task's **Stopped reason**. Typical: cannot pull the image (endpoints, tag missing), or the container exits at startup. |
| Container exits at startup | CloudWatch Logs `/plan/<env>/web`. `ConfigError` names the setting. `interviewer_bedrock` without an approved model is the likely one. |
| Targets unhealthy | EC2, Target groups, `plan-...`, Targets tab. Health check is `GET /api/health` on port 8000. |
| Smoke test times out | Same as above, plus DNS (is the name pointing at the ALB?) and the certificate. |
| Entry page answers `503` | The access code was never set, or the task role cannot read the secret or use the key. Logs say which category. |
| Users get `403` | WAF. CloudWatch metrics, namespace `AWS/WAFV2`, `BlockedRequests` per rule. Turn sampled requests on briefly to see what matched. |
| Entry page refuses with `403` after many attempts | The sensitive rate limit (default 100 / 5 min / IP, blocks for the rest of the window): raise `RATE_LIMIT_SENSITIVE`. |
| Sweeper alarm email | ECS console, cluster, stopped tasks of family `plan-<env>-sweeper`; logs `/plan/<env>/sweeper` (`expiry_run`, `expiry_delete_failed`). |
| Cost alert | Cost Explorer, group by tag `Environment` (needs the tag activated for cost allocation). |
| Destroy leaves something | `scripts/verify-destroyed.sh <env>` prints what. |
| Deploy fails: secret "scheduled for deletion" | A previous destroy did not purge it. `aws secretsmanager delete-secret --secret-id plan-<env>/<name> --force-delete-without-recovery`, then redeploy. |

## Checks you can run locally

```bash
cd infra && uv sync && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```
The tests synthesise `test` and `prod` and assert the important properties (table, key,
network, rules, roles, schedule, outputs, removal policies, missing `image_tag`). CI runs the same
in the `infra` job. To see what a deploy would change in a real account:
`scripts/cdk-run.sh diff test service` with the variables above exported (`IMAGE_TAG` too).

CI also runs `pip-audit` on the backend lockfile and `npm audit --omit=dev --audit-level=high`
on the frontend in a **non-blocking** job (a new advisory against unchanged code should not
fail unrelated work; read its log). The ECR image scan gate in the Deploy workflow **is** blocking (CRITICAL by default).
A push to `main` runs CI twice (its own trigger and the Deploy workflow's call). Harmless.

## Cost notes

Estimates from memory of public prices, not from the AWS bill or calculator: check
[the pricing pages](https://aws.amazon.com/pricing/) for Sydney before relying on them.

* **Fixed per environment** (roughly US$150 to 220 a month, mostly these): the 5 interface
  endpoints in 2 AZs (10 endpoint-hours billed per hour, about US$70 to 90 a month, the
  largest single item), 2 Fargate tasks at 0.5 vCPU / 1 GB (about US$35 to 45), the ALB
  (about US$20 to 25), WAF (a web ACL, 4 rules and request charges, about US$10 to 15), and
  small amounts for the KMS key, 2 secrets, logs and alarms.
* **Variable**: Bedrock tokens (the likely dominant cost once people use it), data processed
  by the endpoints and ALB, the sweeper (96 short tasks a day, a few dollars at most).
* The budget is **account-wide** (test and prod share the account), US$450 by default, alerting
  at 80% actual and 80% forecast to `ALARM_EMAIL`. It does not stop anything.
* `test` can be destroyed when not in use (rebuild is one workflow plus the access code).

## Open decisions

1. **Email (SES) is off, and cannot work as designed.** The tasks are in isolated subnets and
   there is no VPC endpoint for the SES API and no NAT, so a send would time out. Setting
   `ENABLE_EMAIL=true` is allowed (the role and environment are generated correctly) but the
   stack prints a synth warning and sending will fail. Resolving it needs a decision: a NAT
   gateway or egress proxy (against the design), a small relay in a VPC that has egress, or
   dropping the email feature and using the downloads.
2. **Bedrock actions.** The role gets `bedrock:InvokeModelWithResponseStream` only (the app calls
   ConverseStream and nothing else; [bedrock.md](bedrock.md) forbids plain `InvokeModel`), on the
   given ARNs. [bedrock.md](bedrock.md) also describes adding a `bedrock:InferenceProfileArn`
   condition on the foundation-model ARNs when a cross-region inference profile is used; that is not
   generated yet. Add it when a profile is approved.
3. **`python -m app.ai.smoke`** (required by bedrock.md before approval) calls the Bedrock
   control plane, which has no endpoint here and is not granted to the task role. Run it
   from somewhere with access, or add a temporary role and endpoint.
4. **CDK bootstrap role breadth** (see prerequisites): the deploy role is narrow, but what it
   can trigger is bounded by the CloudFormation execution role. Both environments' roles can
   assume the same bootstrap roles, so `test` credentials could technically deploy `prod`; the
   GitHub Environment trust (and prod's reviewers) is what separates them.
5. **No CloudTrail**, WAF logs, ALB access logs or VPC flow logs here. CloudTrail is
   account-level and should exist already. The others were left out on privacy grounds (they
   record client addresses and paths); add them deliberately if wanted.
6. SNS topic is not encrypted with the key (EventBridge and CloudWatch alarms could not publish
   to it otherwise without extra key policy). Messages carry no content.
7. **No third-party scanner action, and every action is pinned to a commit.** The workflows used
   `aquasecurity/trivy-action`; its tags (and those of `setup-trivy`) were hijacked in March 2026
   (CVE-2026-33634), and its original tags were later deleted. The first run's Trivy install step failed
   (cause not confirmed; it may be related). Trivy is gone from the pipeline. The image is scanned by ECR's scan-on-push instead
   (what AWS can scan: OS packages and the languages it supports, so it is a gate, not a full audit),
   and every remaining action (`checkout`, `setup-node`, `setup-uv`, `upload-artifact`,
   `configure-aws-credentials`) is pinned to the exact commit its tag pointed at, with the tag in a
   comment. Pins do not update themselves: bump them on purpose (for example with Dependabot).
   **No SBOM is produced now** (it came from Trivy). Add one back from a source you trust.
8. Availability zones default to `<region>a` and `<region>b`. If an account does not have both
   in the region, set `availability_zones`.

## Not verified

None of the following has been run. Treat each as a likely first-deploy surprise.

* **Anything on AWS.** No stack was deployed; no CloudFormation, ECS, ALB, WAF, Scheduler,
  Budgets, EventBridge or IAM behaviour was observed. Only offline synthesis (CDK CLI
  2.1147.0 with `aws-cdk-lib` 2.275.0), `cfn-lint` and unit tests ran.
* The GitHub workflows were only checked statically (`actionlint`, `shellcheck`). Not run on
  GitHub: the OIDC assumption, the ECR scan gate (`aws ecr wait image-scan-complete` and the findings
  query), environment variables reaching the job, concurrency behaviour.
* The deploy role's permissions are untested against a real `cdk deploy` (the CLI's bootstrap
  version check, asset publishing, reading outputs). A missing action will show as `AccessDenied`.
* `/tmp` on Fargate: whether the ephemeral volume is writable by uid 10001 on a read-only root
  filesystem. If the app fails writing a temp file, that is it.
* The container starting and passing the ALB health check in isolated subnets: image pull
  through the ECR and S3 endpoints, Secrets Manager and DynamoDB reachability, private DNS.
* WAF behaviour: false positives from the managed rules on real chat text and uploads, the
  `SizeRestrictions_BODY` override, the rate limits' effect behind a shared egress IP.
* ALB idle timeout (120 s) against real streaming replies.
* Scheduler `RunTask` with `PassRole`; the EventBridge pattern (`group` is
  `family:<family>` for scheduled tasks, `$or` with `anything-but`); that a non-zero exit
  triggers the rule and the alarm email arrives.
* The sweeper against the real table: its permission set was taken from the brief, not from a
  run, and a memory size of 512 MiB is a guess.
* AWS Budgets created through CloudFormation from `ap-southeast-2`.
* Secret handling: that `--force-delete-without-recovery` works on a secret CloudFormation just
  deleted, and that a later CloudFormation update to either secret never rewrites its value.
  The access-code secret carries a placeholder `SecretString` in the template (as designed); if
  an update to that resource were to re-apply it, the real hash would be overwritten and entry
  would answer `503` until `set-access-secrets.sh` is run again. Avoid editing that secret's
  definition without checking.
* `scripts/set-access-secrets.sh` against real Secrets Manager (tested with a stand-in `aws`:
  the hash is valid Argon2 and verifies, and the code appears nowhere in what `aws` receives). `file:///dev/stdin` as the value of `--secret-string` is assumed to work with your AWS CLI (the equivalent for
  `--cli-input-json` did NOT: see the next item).
* The destroy workflow end to end, including the leftover Lambda log group removal and the
  tag-based check (the tagging API can lag; the script retries).
* **Parameter Store backend.** `ssm put-parameter --cli-input-json file:///dev/stdin` does not work with a real
  AWS CLI (first real run: "Invalid JSON received"), so the script now sends the request with the AWS
  library for Python (boto3, from the backend's own environment): tested against a simulated Parameter Store
  for the create with tags, the overwrite and the stored value, not yet against AWS with SSO credentials. Also
  unverified: that the account's SCPs allow `ssm:PutParameter` with `--type SecureString` under
  `alias/aws/ssm` as the probe suggests, and `ssm:GetParameter` from the task role; that the app
  reads the parameter names the stacks pass; the `ssm` interface endpoint's private DNS in the full
  profile; that the Destroy workflow's `ssm:DeleteParameters` is enough and that the leftover check
  runs without `ssm:GetParameters`. The `SECRETS_BACKEND` environment variable is set only for
  `parameterstore` (Secrets Manager is the app's default).
* **Minimal profile** (never deployed either): tasks with public IPs pulling from ECR and reaching
  DynamoDB, Secrets Manager and Bedrock over the internet; the task security group's DNS rules
  (the VPC resolver is not filtered by security groups, so they are probably redundant); that
  `ECS` accepts `AvailabilityZoneRebalancing` being left unset for one task; DynamoDB with AWS-owned
  encryption (the template carries `SSEEnabled: false`, which is how CDK expresses it).
* The minimal permission list is derived from the resource types in the synthesised templates, not
  from a real deployment under a restricted principal; expect a missing action or two.
* `scripts/make-self-signed-cert.sh`: tested with a stand-in `aws` (the key is mode 600, in a
  directory that is gone afterwards, never printed; the SAN is present). Not verified: that ACM
  accepts the certificate as generated (the macOS `openssl` is LibreSSL), that the ALB serves it,
  and how Chrome and Edge treat the app's `Secure` / `__Host-` cookies on a self-signed page.
* The `docker build --platform linux/amd64` command for Apple silicon was not run.
* Costs above.
