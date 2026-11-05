#!/usr/bin/env bash
# After `cdk destroy`: confirm both stacks are gone and no tagged resources remain.
#
#   scripts/verify-destroyed.sh <test|prod> [region]
#
# Needs cloudformation:DescribeStacks and tag:GetResources. The KMS key stays in its 7-day
# pending-deletion window by design, and is reported, not counted as a failure.
set -euo pipefail

env_name=${1:?usage: verify-destroyed.sh <test|prod> [region]}
region=${2:-${AWS_REGION:-ap-southeast-2}}
failed=0

for stack in "plan-${env_name}-service" "plan-${env_name}-foundation"; do
  if out=$(aws cloudformation describe-stacks --region "$region" --stack-name "$stack" 2>&1); then
    echo "STILL PRESENT: stack ${stack}" >&2
    failed=1
  elif grep -q "does not exist" <<<"$out"; then
    echo "gone: stack ${stack}"
  else
    echo "Could not check ${stack}: ${out}" >&2
    failed=1
  fi
done

# Every resource is tagged Project=plan-on-a-page and Environment=<env> by the CDK app.
# The tagging API lags behind deletions by a minute or two, so look again before failing.
for attempt in 1 2 3; do
  found=$(aws resourcegroupstaggingapi get-resources --region "$region" \
    --tag-filters "Key=Project,Values=plan-on-a-page" "Key=Environment,Values=${env_name}" \
    --query 'ResourceTagMappingList[].ResourceARN' --output text | tr '\t' '\n' | sed '/^$/d')
  leftover=$(grep -v ':kms:' <<<"$found" || true)
  [ -z "$leftover" ] && break
  [ "$attempt" -lt 3 ] && sleep 30
done
if [ -n "$leftover" ]; then
  echo "Tagged resources still present:" >&2
  echo "$leftover" >&2
  failed=1
else
  echo "gone: no tagged resources (other than the KMS key)"
fi
if grep -q ':kms:' <<<"$found"; then
  echo "note: the KMS key is in its 7-day pending-deletion window and disappears by itself."
fi
exit "$failed"
