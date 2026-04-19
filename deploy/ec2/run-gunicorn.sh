#!/bin/bash
# Load optional JWT from SSM (see inject_ssm_jwt.py), then exec Gunicorn.
# EnvironmentFile in systemd already loaded /opt/saathi/backend/.env; /run/saathi-jwt.env overrides JWT_SECRET when SSM is configured.
set -euo pipefail
/opt/saathi/venv/bin/python /opt/saathi/deploy/ec2/inject_ssm_jwt.py
if [[ -f /run/saathi-jwt.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /run/saathi-jwt.env
  set +a
fi
exec /opt/saathi/venv/bin/gunicorn -c /opt/saathi/deploy/ec2/gunicorn.conf.py app.main:app
