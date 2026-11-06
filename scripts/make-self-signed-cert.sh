#!/usr/bin/env bash
# Make a self-signed certificate and import it into ACM, for a TEST deployment with no domain.
#
#   AWS_PROFILE=<profile> scripts/make-self-signed-cert.sh [hostname] [region]
#
# hostname: the SAN (default plan-test.invalid). The ALB's own DNS name is not known before the
# first deploy, so a placeholder is fine: the browser will warn about the certificate either way
# (it is self-signed, and the name will not match), which is expected for a test. Click through
# it. Never use this for real users or real content.
#
# Creates an RSA 2048 key and a 365 day certificate with openssl, imports both with
# `aws acm import-certificate` in the region (default ap-southeast-2, where the ALB is), and
# prints the certificate ARN: set it as CERTIFICATE_ARN. The private key lives only in a
# mode-700 temp directory that is deleted when the script exits, and is never printed. Needs
# acm:ImportCertificate only. Delete the certificate yourself when the test ends
# (`aws acm delete-certificate`): destroying the stacks does not own it.
set -euo pipefail
umask 077

host=${1:-plan-test.invalid}
region=${2:-${AWS_REGION:-ap-southeast-2}}
case "$host" in
  *[!A-Za-z0-9.-]* | '' | .* | *..*) echo "hostname may contain only letters, digits, dots and hyphens" >&2; exit 2 ;;
esac

dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT
chmod 700 "$dir"

openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 365 \
  -keyout "$dir/key.pem" -out "$dir/cert.pem" \
  -subj "/CN=${host}/O=Plan on a Page TEST (self-signed)" \
  -addext "subjectAltName=DNS:${host}" 2> "$dir/openssl.log" \
  || { cat "$dir/openssl.log" >&2; exit 1; }
chmod 600 "$dir/key.pem" "$dir/cert.pem"

arn=$(aws acm import-certificate --region "$region" \
  --certificate "fileb://$dir/cert.pem" --private-key "fileb://$dir/key.pem" \
  --query CertificateArn --output text)

echo "Imported a self-signed certificate for ${host} (valid 365 days) in ${region}." >&2
echo "Set this as CERTIFICATE_ARN:" >&2
echo "$arn"
