#!/bin/bash
# Fetch RDS master credentials from Secrets Manager and append DATABASE_URL to backend/.env.
# Prerequisite: EC2 instance profile allows secretsmanager:GetSecretValue on this secret.
#
# Usage:
#   export RDS_SECRET_ARN="arn:aws:secretsmanager:ap-south-1:ACCOUNT:secret:rds!..."
#   sudo -E ./write-database-url.sh
#
# Or: RDS_SECRET_ARN=... ./write-database-url.sh /opt/saathi/backend/.env

set -euo pipefail

ENV_FILE="${1:-/opt/saathi/backend/.env}"
SECRET_ARN="${RDS_SECRET_ARN:-}"

if [[ -z "${SECRET_ARN}" ]]; then
  echo "RDS_SECRET_ARN is not set." >&2
  exit 1
fi

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

TMP_JSON="$(mktemp)"
trap 'rm -f "${TMP_JSON}"' EXIT

aws secretsmanager get-secret-value \
  --secret-id "${SECRET_ARN}" \
  --query SecretString \
  --output text > "${TMP_JSON}"

export TMP_JSON
# Optional: if the secret JSON has no host (some formats), set from Terraform output, e.g.
#   export RDS_ENDPOINT='saathi-postgres.xxx.ap-south-1.rds.amazonaws.com:5432'
# or host only:
#   export RDS_HOST='saathi-postgres.xxx.ap-south-1.rds.amazonaws.com'
DATABASE_URL="$(python3 <<'PY'
import json, os, urllib.parse

path = os.environ["TMP_JSON"]
with open(path, encoding="utf-8") as f:
    data = json.load(f)

user = data.get("username")
password = data.get("password")
if not user or not password:
    raise SystemExit("Secret JSON must include username and password")

def _parse_tf_endpoint(raw):
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if ":" in raw:
        host_part, port_part = raw.rsplit(":", 1)
        if port_part.isdigit():
            return host_part.strip(), int(port_part)
    return raw, None

env_host = (os.environ.get("RDS_HOST") or "").strip() or None
env_port = os.environ.get("RDS_PORT")
env_ep = (os.environ.get("RDS_ENDPOINT") or "").strip()
eh, ep = _parse_tf_endpoint(env_ep)
if env_host is None:
    env_host = eh
env_port_i = int(env_port) if (env_port or "").strip().isdigit() else ep

host = (data.get("host") or data.get("hostname") or env_host or "").strip()
port = data.get("port")
if port is None:
    port = env_port_i
if port is None:
    port = 5432
else:
    port = int(port)

dbname = (data.get("dbname") or data.get("database") or os.environ.get("RDS_DB_NAME") or "saathi")
dbname = str(dbname).strip()

if not host:
    raise SystemExit(
        "Could not determine RDS host: secret has no host/hostname. "
        "Set RDS_HOST or RDS_ENDPOINT (from terraform output rds_endpoint) and re-run."
    )

pwd_q = urllib.parse.quote_plus(str(password))
base = f"postgresql://{user}:{pwd_q}@{host}:{port}/{dbname}"
sep = "&" if "?" in base else "?"
print(f"{base}{sep}sslmode=require")
PY
)"

LINE="DATABASE_URL=${DATABASE_URL}"
mkdir -p "$(dirname "${ENV_FILE}")"
if [[ -f "${ENV_FILE}" ]] && grep -q '^DATABASE_URL=' "${ENV_FILE}"; then
  echo "Replacing existing DATABASE_URL in ${ENV_FILE}"
  grep -v '^DATABASE_URL=' "${ENV_FILE}" > "${ENV_FILE}.new"
  echo "${LINE}" >> "${ENV_FILE}.new"
  mv "${ENV_FILE}.new" "${ENV_FILE}"
else
  echo "${LINE}" >> "${ENV_FILE}"
  echo "Wrote DATABASE_URL to ${ENV_FILE}"
fi
