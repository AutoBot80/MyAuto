#!/bin/bash
# Merge DATABASE_URL from the RDS master-user secret (Secrets Manager) into backend .env.
# Re-starts saathi-api only if DATABASE_URL actually changed and the service was already running.
#
# Config file (one path): default /opt/saathi/etc/rds-url-sync.env
#   RDS_SECRET_ARN=arn:aws:secretsmanager:...   (required; same as write-database-url.sh)
#   RDS_HOST=... or RDS_ENDPOINT=host:port       (optional if secret JSON has host)
#   RDS_DB_NAME=saathi                           (optional)
#   AWS_DEFAULT_REGION=ap-south-1                 (optional if already in environment)
#
# Usage: sudo -E /opt/saathi/deploy/ec2/sync-database-url.sh
#   Systemd: saathi-rds-url-sync.service + saathi-rds-url-sync.timer (see deploy/ec2)

set -euo pipefail

SYNC_CFG="${SYNC_CONFIG:-/opt/saathi/etc/rds-url-sync.env}"
REPO_WRITER="/opt/saathi/deploy/ec2/write-database-url.sh"
ENV_FILE="${ENV_TARGET:-/opt/saathi/backend/.env}"

if [[ ! -f "$SYNC_CFG" ]]; then
  echo "sync-database-url: $SYNC_CFG not found, skipping (add file to enable auto-sync from RDS secret)" >&2
  exit 0
fi

if [[ ! -f "$REPO_WRITER" ]]; then
  echo "sync-database-url: missing $REPO_WRITER" >&2
  exit 1
fi
chmod +x "$REPO_WRITER" 2>/dev/null || true

set -a
# shellcheck disable=SC1090
source "$SYNC_CFG"
set +a

: "${RDS_SECRET_ARN:?RDS_SECRET_ARN is required in $SYNC_CFG}"

if [[ -z "${AWS_DEFAULT_REGION:-}" ]] && [[ -n "${AWS_REGION:-}" ]]; then
  export AWS_DEFAULT_REGION="${AWS_REGION}"
fi
if [[ -z "${AWS_DEFAULT_REGION:-}" ]]; then
  echo "sync-database-url: set AWS_DEFAULT_REGION in $SYNC_CFG or the environment" >&2
  exit 1
fi

OLDLINE=""
if [[ -f "$ENV_FILE" ]]; then
  OLDLINE=$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" || true)
fi

export RDS_SECRET_ARN
"$REPO_WRITER" "$ENV_FILE"
NEWLINE=$(grep -m1 '^DATABASE_URL=' "$ENV_FILE" || true)
if [[ "$OLDLINE" == "$NEWLINE" ]]; then
  echo "sync-database-url: DATABASE_URL unchanged"
  exit 0
fi
echo "sync-database-url: DATABASE_URL updated from Secrets Manager"
if systemctl is-active --quiet saathi-api 2>/dev/null; then
  systemctl restart saathi-api
  echo "sync-database-url: restarted saathi-api"
else
  echo "sync-database-url: saathi-api is not active, not restarting (e.g. first boot before unit start)"
fi
