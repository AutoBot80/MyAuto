#!/usr/bin/env python3
"""Optional: load JWT_SECRET from SSM Parameter Store before Gunicorn starts.

If ``JWT_SSM_PARAMETER_NAME`` is unset or empty in ``/opt/saathi/backend/.env``,
this script removes ``/run/saathi-jwt.env`` and exits 0 (JWT comes only from .env).

If set, fetches the SecureString value and writes ``/run/saathi-jwt.env`` with
``JWT_SECRET=...`` so systemd can load it *after* ``.env`` (override).

Requires: AWS CLI on PATH and instance role with ``ssm:GetParameter`` on the parameter.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ENV_FILE = "/opt/saathi/backend/.env"
RUN_ENV = "/run/saathi-jwt.env"


def _read_jwt_ssm_param_name() -> str:
    if not os.path.isfile(ENV_FILE):
        return ""
    text = open(ENV_FILE, encoding="utf-8", errors="replace").read()
    m = re.search(r"(?m)^JWT_SSM_PARAMETER_NAME=(.*)$", text)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def _systemd_escape_value(s: str) -> str:
    if re.search(r'[\s#$"\'\\]', s):
        esc = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{esc}"'
    return s


def main() -> int:
    param = _read_jwt_ssm_param_name()
    if not param:
        try:
            os.remove(RUN_ENV)
        except FileNotFoundError:
            pass
        return 0

    try:
        out = subprocess.check_output(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--name",
                param,
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as e:
        print(e.stderr or str(e), file=sys.stderr)
        return 1

    if not out:
        print("SSM parameter returned empty value.", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(RUN_ENV), exist_ok=True)
    with open(RUN_ENV, "w", encoding="utf-8") as f:
        f.write("JWT_SECRET=" + _systemd_escape_value(out) + "\n")
    os.chmod(RUN_ENV, 0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
