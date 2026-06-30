"""
Capture dealer-PC environment metadata for Playwright execution logs (DMS, insurance).

Written at the top of ``Playwright_DMS_*.txt`` and ``Playwright_insurance_*.txt`` so operators
can see which machine, app versions, and network connection were active when automation started.
"""
from __future__ import annotations

import io
import os
import re
import socket
import subprocess
import sys
from typing import Any, TextIO

from app.version import BACKEND_SEMVER, GIT_COMMIT_SHORT

_NETSH_TIMEOUT_SEC = 4
_POWERSHELL_TIMEOUT_SEC = 6


def resolve_dealer_pc_hostname() -> str:
    """Windows computer name (COMPUTERNAME) or socket hostname."""
    for key in ("COMPUTERNAME", "HOSTNAME"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    try:
        return (socket.gethostname() or "").strip()
    except OSError:
        return ""


def _parse_netsh_wlan_ssid(stdout: str) -> str:
    """Parse ``SSID : …`` from ``netsh wlan show interfaces`` output."""
    for line in (stdout or "").splitlines():
        m = re.match(r"^\s*SSID\s*:\s*(.+)\s*$", line, re.IGNORECASE)
        if not m:
            continue
        ssid = m.group(1).strip()
        if ssid and ssid.lower() not in ("", "n/a"):
            return ssid
    return ""


def _run_subprocess_text(cmd: list[str], *, timeout_sec: int) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
        return (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def resolve_internet_connection_name() -> str:
    """
    Active Windows network connection name (Wi‑Fi SSID such as ``Arya_5GHz``, or profile name).

    Primary: ``netsh wlan show interfaces``. Fallback: ``Get-NetConnectionProfile``.
    """
    if sys.platform != "win32":
        return ""

    netsh_out = _run_subprocess_text(
        ["netsh", "wlan", "show", "interfaces"],
        timeout_sec=_NETSH_TIMEOUT_SEC,
    )
    ssid = _parse_netsh_wlan_ssid(netsh_out)
    if ssid:
        return ssid

    ps_cmd = (
        "(Get-NetConnectionProfile | Where-Object IPv4Connectivity -ne 'Disconnected' "
        "| Select-Object -First 1).Name"
    )
    profile = _run_subprocess_text(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        timeout_sec=_POWERSHELL_TIMEOUT_SEC,
    )
    return profile.strip()


def build_playwright_run_environment(*, job_params: dict[str, Any] | None = None) -> dict[str, str]:
    """Merge local OS fields, backend version, and optional client-passed params."""
    params = job_params or {}
    frontend = (params.get("client_app_version") or "").strip()
    client_api = (params.get("client_api_base_url") or "").strip()
    return {
        "dealer_pc_hostname": resolve_dealer_pc_hostname(),
        "backend_version": BACKEND_SEMVER,
        "frontend_version": frontend,
        "internet_connection_name": resolve_internet_connection_name(),
        "backend_git_commit": GIT_COMMIT_SHORT,
        "client_api_base_url": client_api[:400] if client_api else "",
    }


def write_playwright_run_environment_header(fp: TextIO, env: dict[str, str]) -> None:
    """Write the fixed ``--- run_environment ---`` block."""
    fp.write("--- run_environment ---\n")
    fp.write(f"dealer_pc_hostname={env.get('dealer_pc_hostname', '')!r}\n")
    fp.write(f"backend_version={env.get('backend_version', '')!r}\n")
    fp.write(f"frontend_version={env.get('frontend_version', '')!r}\n")
    internet = (env.get("internet_connection_name") or "").strip()
    if internet:
        fp.write(f"internet_connection_name={internet!r}\n")
    else:
        fp.write("internet_connection_name=''\n")
        fp.write("# internet_connection_name unavailable (not connected or lookup failed)\n")
    fp.write(f"backend_git_commit={env.get('backend_git_commit', '')!r}\n")
    client_api = (env.get("client_api_base_url") or "").strip()
    if client_api:
        fp.write(f"client_api_base_url={client_api!r}\n")
    fp.write("--- end run_environment ---\n\n")


def format_playwright_run_environment_header(env: dict[str, str]) -> str:
    """Return the environment header as a string (for tests)."""
    buf = io.StringIO()
    write_playwright_run_environment_header(buf, env)
    return buf.getvalue()
