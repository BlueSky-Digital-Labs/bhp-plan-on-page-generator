#!/usr/bin/env bash
# Print the secrets backend an environment uses: secretsmanager or parameterstore.
#
#   SECRETS_BACKEND=... PROFILE=... scripts/secrets-backend.sh <test|prod>
#
# The same rule as infra/config.py: an explicit SECRETS_BACKEND wins, else parameterstore for the
# minimal profile and secretsmanager for full. The profile is PROFILE, else minimal for test and
# full for prod. Keep the three in step.
set -euo pipefail

env_name=${1:?usage: secrets-backend.sh <test|prod>}
case "$env_name" in test) default_profile=minimal ;; prod) default_profile=full ;; *) echo "environment must be test or prod" >&2; exit 2 ;; esac
profile=${PROFILE:-$default_profile}
case "$profile" in minimal) default_backend=parameterstore ;; full) default_backend=secretsmanager ;; *) echo "PROFILE must be full or minimal" >&2; exit 2 ;; esac
backend=${SECRETS_BACKEND:-$default_backend}
case "$backend" in secretsmanager | parameterstore) echo "$backend" ;; *) echo "SECRETS_BACKEND must be secretsmanager or parameterstore" >&2; exit 2 ;; esac
