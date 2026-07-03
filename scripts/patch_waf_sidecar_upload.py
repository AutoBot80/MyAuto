#!/usr/bin/env python3
"""
Surgical WAF update: add /sidecar to upload body-rule exclusions (CloudFront Web ACL).

Does not use Terraform. Does not touch EC2 alarms or launch templates.

Usage (from repo root, AWS creds for us-east-1):
  python scripts/patch_waf_sidecar_upload.py
  python scripts/patch_waf_sidecar_upload.py --dry-run
"""

from __future__ import annotations

import argparse
import copy
import sys

SIDECAR_MATCH = {
    "ByteMatchStatement": {
        "SearchString": "/sidecar",
        "FieldToMatch": {"UriPath": {}},
        "TextTransformations": [{"Priority": 0, "Type": "LOWERCASE"}],
        "PositionalConstraint": "STARTS_WITH",
    },
}


def _byte_match(st: dict) -> dict | None:
    if "ByteMatchStatement" in st:
        return st["ByteMatchStatement"]
    inner = st.get("Statement") or {}
    return inner.get("ByteMatchStatement")


def _has_sidecar(or_stmt: dict) -> bool:
    for st in or_stmt.get("Statements") or []:
        bm = _byte_match(st)
        if bm and bm.get("SearchString") == "/sidecar":
            return True
    return False


def _add_sidecar(or_stmt: dict) -> bool:
    if _has_sidecar(or_stmt):
        return False
    stmts = list(or_stmt.get("Statements") or [])
    stmts.append(SIDECAR_MATCH)
    or_stmt["Statements"] = stmts
    return True


def _patch_rule(rule: dict, rule_name: str) -> bool:
    if rule.get("Name") != rule_name:
        return False
    mrg = (rule.get("Statement") or {}).get("ManagedRuleGroupStatement") or {}
    if rule_name == "AWSManagedRulesCommonRuleSet":
        or_stmt = (
            mrg.get("ScopeDownStatement", {})
            .get("NotStatement", {})
            .get("Statement", {})
            .get("OrStatement")
        )
    elif rule_name == "CommonRuleSetUploadsExclusions":
        or_stmt = mrg.get("ScopeDownStatement", {}).get("OrStatement")
    else:
        return False
    if not or_stmt:
        return False
    return _add_sidecar(or_stmt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="saathi-cf-waf")
    parser.add_argument("--id", default="3d584372-40a7-443f-b835-2cf6f9b1555f")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        import boto3
    except ImportError:
        print("boto3 required: pip install boto3", file=sys.stderr)
        return 1

    client = boto3.client("wafv2", region_name=args.region)
    resp = client.get_web_acl(Name=args.name, Scope="CLOUDFRONT", Id=args.id)
    acl = copy.deepcopy(resp["WebACL"])
    lock = resp["LockToken"]

    changed: list[str] = []
    rules = acl.get("Rules") or []
    for rule in rules:
        if _patch_rule(rule, "AWSManagedRulesCommonRuleSet"):
            changed.append("AWSManagedRulesCommonRuleSet")
        if _patch_rule(rule, "CommonRuleSetUploadsExclusions"):
            changed.append("CommonRuleSetUploadsExclusions")

    if not changed:
        print("No change: /sidecar already in upload exclusion rules.")
        return 0

    print("Will add /sidecar to:", ", ".join(changed))
    if args.dry_run:
        print("Dry run — not calling update_web_acl.")
        return 0

    client.update_web_acl(
        Name=args.name,
        Scope="CLOUDFRONT",
        Id=args.id,
        LockToken=lock,
        DefaultAction=acl["DefaultAction"],
        Description=acl.get("Description", ""),
        Rules=rules,
        VisibilityConfig=acl["VisibilityConfig"],
    )
    print("OK: Web ACL updated. Wait 1–2 minutes, then retry upload test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
