#!/usr/bin/env bash
# Run one CDK command for one environment, passing settings as CDK context.
#
#   scripts/cdk-run.sh <synth|diff|deploy|destroy> <test|prod> <foundation|service> [cdk args...]
#
# Settings come from environment variables (the workflows fill them from GitHub Environment
# variables). Unset or empty ones are left out, so the defaults in infra/config.py apply.
# IMAGE_TAG is needed for the service stack. Needs the CDK CLI (npm i -g aws-cdk) and uv.
set -euo pipefail

[ "$#" -ge 3 ] || { sed -n '2,9p' "$0" >&2; exit 2; }
command_name=$1 env_name=$2 target=$3
shift 3
case "$target" in foundation | service) ;; *) echo "stack must be foundation or service" >&2; exit 2 ;; esac

args=(-c "env=${env_name}")
add() { # add <context key> <environment variable>
  local value=${!2:-}
  if [ -n "$value" ]; then args+=(-c "$1=$value"); fi
}
add profile PROFILE
add secrets_backend SECRETS_BACKEND
add region AWS_REGION
add availability_zones AVAILABILITY_ZONES
add vpc_id VPC_ID
add public_subnet_ids PUBLIC_SUBNET_IDS
add certificate_arn CERTIFICATE_ARN
add domain_name DOMAIN_NAME
add hosted_zone_id HOSTED_ZONE_ID
add image_tag IMAGE_TAG
add bedrock_model_id BEDROCK_MODEL_ID
add bedrock_resource_arns BEDROCK_RESOURCE_ARNS
add bedrock_role_arn BEDROCK_ROLE_ARN
add bedrock_log_error_text BEDROCK_LOG_ERROR_TEXT
add bedrock_region BEDROCK_REGION
add alarm_email ALARM_EMAIL
add monthly_budget_usd MONTHLY_BUDGET_USD
add app_name APP_NAME
add header_text HEADER_TEXT
add footer_text FOOTER_TEXT
add web_desired_count WEB_DESIRED_COUNT
add enable_email ENABLE_EMAIL
add ses_identity_arn SES_IDENTITY_ARN
add email_sender_address EMAIL_SENDER_ADDRESS
add email_allowed_domains EMAIL_ALLOWED_DOMAINS
add ses_region SES_REGION
add email_log_error_text EMAIL_LOG_ERROR_TEXT
add rate_limit_sensitive RATE_LIMIT_SENSITIVE
add rate_limit_general RATE_LIMIT_GENERAL

cd "$(dirname "$0")/../infra"
exec cdk "$command_name" "plan-${env_name}-${target}" "${args[@]}" --no-notices "$@"
