#!/bin/bash
# Pull the full backend .env from AWS Secrets Manager and write it to disk.
# Called by user_data on every instance boot (ASG launch, scale-out, refresh).
#
# Usage:
#   DOTENV_SECRET_ARN="arn:aws:secretsmanager:ap-south-1:123456789012:secret:saathi/production/dotenv-AbCdEf" \
#     ./load-dotenv.sh [/opt/saathi/backend/.env]

set -euo pipefail

ENV_FILE="${1:-/opt/saathi/backend/.env}"
SECRET_ARN="${DOTENV_SECRET_ARN:-}"

if [[ -z "${SECRET_ARN}" ]]; then
  echo "DOTENV_SECRET_ARN is not set — skipping .env load from Secrets Manager." >&2
  exit 0
fi

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }

mkdir -p "$(dirname "${ENV_FILE}")"

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
AWS_ARGS=()
if [[ -n "${REGION}" ]]; then
  AWS_ARGS+=(--region "${REGION}")
fi

TMP="$(mktemp)"
trap 'rm -f "${TMP}"' EXIT
if ! aws "${AWS_ARGS[@]}" secretsmanager get-secret-value \
  --secret-id "${SECRET_ARN}" \
  --query SecretString \
  --output text > "${TMP}"; then
  echo "aws secretsmanager get-secret-value failed (check IAM, ARN, and region)." >&2
  exit 1
fi
if [[ ! -s "${TMP}" ]]; then
  echo "SecretString was empty — refusing to write .env" >&2
  exit 1
fi
mv -f "${TMP}" "${ENV_FILE}"
trap - EXIT
chmod 600 "${ENV_FILE}"
echo "Wrote .env ($(wc -l < "${ENV_FILE}") lines) from Secrets Manager to ${ENV_FILE}"
