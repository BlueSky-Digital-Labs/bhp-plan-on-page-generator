#!/usr/bin/env bash
# Set the shared access code (and, once, the cookie signing key) for an environment.
#
#   AWS_PROFILE=<profile> scripts/set-access-secrets.sh <test|prod> [region] [--rotate-cookie-key]
#
# Prompts for the code with hidden input, hashes it with Argon2 (the same library the app
# verifies with) and stores only the hash. The code is never echoed, never put on a command
# line, never written to a file, and not kept in history. Uses whatever AWS credentials your
# shell already has.
#
# Backend (SECRETS_BACKEND, else by PROFILE as in infra/config.py; see secrets-backend.sh):
#   parameterstore  SSM SecureString parameters /plan-<env>/access-code-hash and
#                   /plan-<env>/cookie-signing-key, for accounts that deny
#                   secretsmanager:CreateSecret. CloudFormation cannot create these, so this
#                   script does: the hash is overwritten, the cookie key is created only if it
#                   does not exist. Encrypted with alias/aws/ssm, or KMS_KEY_ID if set (use
#                   alias/plan-<env> for the full profile's key). Needs ssm:PutParameter and
#                   ssm:GetParameter on /plan-<env>/*, and kms:Encrypt on KMS_KEY_ID if set.
#   secretsmanager  plan-<env>/access-code-hash (made by CloudFormation). Needs
#                   secretsmanager:PutSecretValue, and the key's kms:GenerateDataKey/Decrypt
#                   when the environment has its own key. The cookie key already exists.
#
# New parameters carry the organisation's tags (infra/org-tags.json). AWS does not allow tags together
# with an overwrite, so tags are set when a parameter is first created and an existing one is only
# overwritten.
#
# --rotate-cookie-key generates a new cookie signing key and stores it. THIS ENDS EVERY SESSION
# AND PENDING GRANT AT ONCE. Without it an existing key is left alone.
set -euo pipefail
umask 077

env_name=
region=
rotate=false
for arg in "$@"; do
  case "$arg" in
    --rotate-cookie-key) rotate=true ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) if [ -z "$env_name" ]; then env_name=$arg; elif [ -z "$region" ]; then region=$arg; else echo "too many arguments" >&2; exit 2; fi ;;
  esac
done
[ -n "$env_name" ] || { echo "usage: set-access-secrets.sh <test|prod> [region] [--rotate-cookie-key]" >&2; exit 2; }
case "$env_name" in test | prod) ;; *) echo "environment must be test or prod" >&2; exit 2 ;; esac
region=${region:-${AWS_REGION:-ap-southeast-2}}
root=$(cd "$(dirname "$0")/.." && pwd)
backend=$("$root/scripts/secrets-backend.sh" "$env_name")
kms_key=${KMS_KEY_ID:-alias/aws/ssm}

hash_name="plan-${env_name}/access-code-hash"
cookie_name="plan-${env_name}/cookie-signing-key"
[ "$backend" = parameterstore ] && { hash_name="/$hash_name"; cookie_name="/$cookie_name"; }

# Reads a value on stdin and prints the JSON for `aws ssm put-parameter --cli-input-json`. The
# value is held in memory only and goes to aws through a pipe, so it is not in any argument.
parameter_json() { # parameter_json <name> <overwrite: true|false> [min length]
  NAME=$1 OVERWRITE=$2 KEY_ID=$kms_key MIN=${3:-1} TAGS_FILE="$root/infra/org-tags.json" python3 -c '
import json, os, sys
value = sys.stdin.read()
if len(value) < int(os.environ["MIN"]):
    sys.exit("value too short")
overwrite = os.environ["OVERWRITE"] == "true"
request = {"Name": os.environ["NAME"], "Value": value, "Type": "SecureString",
           "KeyId": os.environ["KEY_ID"], "Overwrite": overwrite}
if not overwrite:  # AWS rejects tags together with an overwrite
    tags = json.load(open(os.environ["TAGS_FILE"]))
    request["Tags"] = [{"Key": k, "Value": v} for k, v in tags.items()]
print(json.dumps(request))'
}
put_parameter() { # JSON from parameter_json on stdin
  aws ssm put-parameter --region "$region" --cli-input-json file:///dev/stdin \
    --query Version --output text > /dev/null
}
parameter_exists() { # 0 if it exists, 1 if it does not; anything else is an error and stops the script
  local err
  if err=$(aws ssm get-parameter --region "$region" --name "$1" \
    --query Parameter.Version --output text 2>&1 > /dev/null); then
    return 0
  elif grep -q ParameterNotFound <<<"$err"; then
    return 1
  fi
  echo "Could not check $1: ${err}" >&2
  exit 1
}
new_cookie_key() { # 62 or so URL-safe characters, never printed
  openssl rand -base64 48 | tr -d '\n=+/'
}

if [ "$rotate" = true ]; then
  echo "--rotate-cookie-key: this will END EVERY SESSION and pending grant in ${env_name}." >&2
  read -r -p "Type 'end all sessions' to continue: " reply
  [ "$reply" = "end all sessions" ] || { echo "Not confirmed. Nothing changed." >&2; exit 1; }
fi

read -r -s -p "New access code for ${env_name}: " code
echo >&2
read -r -s -p "Repeat it: " again
echo >&2
if [ -z "$code" ] || [ "$code" != "$again" ]; then
  echo "Empty, or the two entries differ. Nothing changed." >&2
  exit 1
fi
if [ "${#code}" -gt 128 ]; then # the app refuses anything longer at the entry page
  echo "The app accepts at most 128 characters. Nothing changed." >&2
  exit 1
fi
unset again

# printf is a shell builtin, so the code is not visible in the process list.
hash=$(printf '%s' "$code" | uv run --quiet --frozen --no-dev --project "$root/backend" python -c \
  "import sys; from argon2 import PasswordHasher; sys.stdout.write(PasswordHasher().hash(sys.stdin.read()))")
unset code
# shellcheck disable=SC2016 # the dollar sign is literal
case "$hash" in '$argon2'*) ;; *) echo "Hashing failed. Nothing changed." >&2; exit 1 ;; esac

if [ "$backend" = parameterstore ]; then
  if parameter_exists "$hash_name"; then
    printf '%s' "$hash" | parameter_json "$hash_name" true | put_parameter
    echo "Updated ${hash_name} in ${region} (key ${kms_key})." >&2
  else
    printf '%s' "$hash" | parameter_json "$hash_name" false | put_parameter
    echo "Created ${hash_name} in ${region} (key ${kms_key}) with the project tags." >&2
  fi
  unset hash

  if [ "$rotate" = true ]; then
    echo "Rotating the cookie signing key: every session and pending grant ends now." >&2
    new_cookie_key | parameter_json "$cookie_name" true 48 | put_parameter
    echo "Replaced ${cookie_name}." >&2
  else
    # Existence only: no decryption, so the value is never read.
    if parameter_exists "$cookie_name"; then
      echo "${cookie_name} already exists; left alone (use --rotate-cookie-key to replace it)." >&2
    else
      new_cookie_key | parameter_json "$cookie_name" false 48 | put_parameter
      echo "Created ${cookie_name} with the project tags." >&2
    fi
  fi
else
  printf '%s' "$hash" | aws secretsmanager put-secret-value --region "$region" \
    --secret-id "$hash_name" --secret-string file:///dev/stdin \
    --query VersionId --output text > /dev/null
  unset hash
  echo "Updated ${hash_name} in ${region}." >&2
  if [ "$rotate" = true ]; then
    echo "Rotating the cookie signing key: every session and pending grant ends now." >&2
    new_cookie_key | aws secretsmanager put-secret-value --region "$region" \
      --secret-id "$cookie_name" --secret-string file:///dev/stdin \
      --query VersionId --output text > /dev/null
    echo "Replaced ${cookie_name}." >&2
  fi
fi
echo "The next entry attempt uses the new hash (no restart needed)." >&2
