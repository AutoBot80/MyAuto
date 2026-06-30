"""Tests for Playwright execution log environment header (dealer PC, versions, Wi‑Fi name)."""
from __future__ import annotations

import sys
from unittest.mock import patch

from app.services.playwright_run_environment import (
    _parse_netsh_wlan_ssid,
    build_playwright_run_environment,
    format_playwright_run_environment_header,
    resolve_internet_connection_name,
)


def test_format_playwright_run_environment_header_includes_all_fields() -> None:
    env = {
        "dealer_pc_hostname": "ARYA-PC",
        "backend_version": "0.9.85",
        "frontend_version": "0.9.85",
        "internet_connection_name": "Arya_5GHz",
        "backend_git_commit": "abc1234",
        "client_api_base_url": "https://api.example.com",
    }
    text = format_playwright_run_environment_header(env)
    assert "--- run_environment ---" in text
    assert "dealer_pc_hostname='ARYA-PC'" in text
    assert "backend_version='0.9.85'" in text
    assert "frontend_version='0.9.85'" in text
    assert "internet_connection_name='Arya_5GHz'" in text
    assert "backend_git_commit='abc1234'" in text
    assert "client_api_base_url='https://api.example.com'" in text
    assert "--- end run_environment ---" in text


def test_format_header_notes_unavailable_internet_name() -> None:
    env = {
        "dealer_pc_hostname": "PC",
        "backend_version": "1.0.0",
        "frontend_version": "",
        "internet_connection_name": "",
        "backend_git_commit": "",
        "client_api_base_url": "",
    }
    text = format_playwright_run_environment_header(env)
    assert "internet_connection_name=''" in text
    assert "internet_connection_name unavailable" in text


def test_parse_netsh_wlan_ssid_arya_5ghz() -> None:
    sample = """
    Name                   : Wi-Fi
    Description            : Intel Wi-Fi 6
    GUID                   : {abc}
    Physical address       : 00:11:22:33:44:55
    State                  : connected
    SSID                   : Arya_5GHz
    BSSID                  : aa:bb:cc:dd:ee:ff
    """
    assert _parse_netsh_wlan_ssid(sample) == "Arya_5GHz"


def test_resolve_internet_connection_name_from_netsh_on_windows() -> None:
    netsh_out = "    SSID                   : Arya_5GHz\n"
    with patch.object(sys, "platform", "win32"):
        with patch(
            "app.services.playwright_run_environment._run_subprocess_text",
            side_effect=[netsh_out, ""],
        ):
            assert resolve_internet_connection_name() == "Arya_5GHz"


def test_resolve_internet_connection_name_non_windows_returns_empty() -> None:
    with patch.object(sys, "platform", "linux"):
        assert resolve_internet_connection_name() == ""


def test_build_playwright_run_environment_merges_job_params() -> None:
    with patch(
        "app.services.playwright_run_environment.resolve_dealer_pc_hostname",
        return_value="DEALER-PC",
    ):
        with patch(
            "app.services.playwright_run_environment.resolve_internet_connection_name",
            return_value="Arya_5GHz",
        ):
            env = build_playwright_run_environment(
                job_params={
                    "client_app_version": "0.9.85",
                    "client_api_base_url": "https://x.test",
                }
            )
    assert env["dealer_pc_hostname"] == "DEALER-PC"
    assert env["frontend_version"] == "0.9.85"
    assert env["internet_connection_name"] == "Arya_5GHz"
    assert env["client_api_base_url"] == "https://x.test"
    assert env["backend_version"]
