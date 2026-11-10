#!/usr/bin/env bash
# Poll <base url>/api/health until it answers 200, or fail.
#
#   scripts/smoke-test.sh https://plan.example.com [--insecure]
#
# --insecure skips certificate verification, for the case with no custom domain, where the ALB's
# own DNS name does not match the ACM certificate. It is only used for this health check.
set -euo pipefail

base=${1:?usage: smoke-test.sh <base url> [--insecure]}
flags=(--silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10)
[ "${2:-}" = "--insecure" ] && flags+=(--insecure)
attempts=${SMOKE_ATTEMPTS:-30}
delay=${SMOKE_DELAY_SECONDS:-10}

for i in $(seq 1 "$attempts"); do
  code=$(curl "${flags[@]}" "${base%/}/api/health" 2>/dev/null || true)
  echo "attempt ${i}/${attempts}: HTTP ${code:-none}"
  [ "$code" = "200" ] && exit 0
  sleep "$delay"
done
echo "${base%/}/api/health never answered 200" >&2
exit 1
