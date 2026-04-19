# Documentation staging changelog (`docs_changelog`)

**Purpose:** Short, append-only notes about **what should change** in canonical docs (**BRD**, **HLD**, **LLD**, **`Database DDL.md`**) *before* those files are edited. Agents and humans can read **only this file** (plus touched code) to plan doc updates instead of re-scanning full BRD/HLD/LLD every time—**lower token use** and faster alignment.

**Not a substitute for:** the formal version/changelog tables inside BRD, HLD, LLD, and Database DDL. Those remain the audit trail after work is merged into documentation.

---

## How to use

1. **During implementation:** Append a row or bullet under **Pending** (below): date, area (`BRD` / `HLD` / `LLD` / `DDL` / `API` / `client`), one-line **what** changed, optional pointers (`backend/...`, `§6.1a`, LLD `6.x`).
2. **When updating canonical docs:** Apply edits to BRD/HLD/LLD/`Database DDL.md`, add their normal changelog rows, then **truncate** the **Pending** section (delete completed items) or replace it with a single **Last synced** line (date + optional git short hash).
3. **Optional:** Keep one line in **Last synced** after each truncate so the next session knows the staging log was cleared intentionally.

---

## Pending

_Add entries below. Remove them after the corresponding BRD/HLD/LLD/DDL updates land._

| Date (IST) | Doc / area | Summary |
|------------|------------|---------|
| 2026-04-15 | New doc / cloud | Added `Production_cloud_design.md`—AWS prod flow (CloudFront→WAF→ALB→ASG), Gunicorn/Nginx, Terraform pointer, deployment BR, ASG max=2 + watcher decision placeholder. |
| 2026-04-19 | Infra / deploy | Expanded ASG launch template `user_data` from stub-only (Nginx health + CW Agent) to full self-deploy (git clone, Python 3.11, venv, pip, Nginx proxy, systemd). Added 4 new Terraform variables (`app_git_repo_url`, `app_git_branch`, `app_github_pat_ssm_param`, `app_dotenv_ssm_param`). Updated `deploy/ec2/README.md`. |
| 2026-04-19 | Backend / deps | Added `python-multipart>=0.0.6` and `gunicorn>=23.0.0` to `backend/requirements.txt` (were missing — caused manual installs on EC2). |
| 2026-04-19 | Infra / alarm | ALB 5xx warning alarm threshold raised from 2 → 5 (`cloudwatch_alarms_alb.tf`). |
| 2026-04-19 | Deploy / fix | `run-gunicorn.sh`: changed SSM inject invocation from `/usr/bin/python3` to `/opt/saathi/venv/bin/python` (Python 3.9 on AL2023 doesn't support 3.10+ syntax in the codebase). |

---

## Last synced

- **2026-04-05** — File created. Prior doc work (e.g. BR-21 Run Report PDFs, `hero_dms_form22_print`, LLD 6.276) is already in BRD/HLD/LLD/DDL; no backlog copied here.
- **2026-04-18** — Handoff alignment: canonical **as-built AWS production** detail is **[`Production_cloud_design.md`](Production_cloud_design.md) §7** (version table §8); [`session_resume.md`](session_resume.md) points there for continuity.

---

## Token / efficiency note

**Yes:** Reading ~1–2 KB here first is cheaper than pulling large BRD/LLD sections repeatedly. **Caveat:** For unfamiliar or cross-cutting changes, you still need the relevant doc sections or codebase; this file reduces *repeat* context, not the need for accuracy checks.
