# Session resume — AWS production / beta (Terraform)

**Saved:** 2026-04-18 (IST). **Region:** `ap-south-1`. **Purpose:** Continue from here after this chat or Cursor window is closed.

---

## Migration status (updated 2026-04-17)

**Done**

- **IaC / network:** Terraform `terraform/network/` applied — VPC, **RDS PostgreSQL 16**, public **ALB**, private **EC2** (SSM, no public IP), IAM instance profile.
- **API:** Gunicorn + FastAPI on EC2 (`saathi-api`), Nginx, **`/health`** OK.
- **Edge:** **CloudFront** + **WAF** (ACM in `us-east-1`) in front of ALB; **`https://api.dealersaathi.co.in`** live.
- **Database:** RDS database name aligned; **core tables** for auth + dealers seeded (**`oem_ref`**, **`dealer_ref`**, **`roles_ref`**, **`login_ref`**, **`login_roles_ref`**). **`argon2-cffi`** on EC2 for password verification.
- **Frontend:** Vite build with **`VITE_API_URL`**, deploy to **S3** `dealersaathi-prod-1980`; **login works** from browser (S3 website origin in **`CORS_ORIGINS`**).
- **Docs:** [`troubleshooting-8000-error.md`](troubleshooting-8000-error.md) (decision tree + EC2/psql commands); [`deploy/frontend-s3-cloudfront.md`](../deploy/frontend-s3-cloudfront.md); [`deploy/ec2/dotenv.production.example`](../deploy/ec2/dotenv.production.example) CORS notes. **As-built AWS (RDS, SNS, alarms, scaling, CW Agent):** [`Production_cloud_design.md`](Production_cloud_design.md) **§7** — also noted in [`docs_changelog.md`](docs_changelog.md) *Last synced* (2026-04-18).

**Pending (migration / ops)**

| Item | Notes |
|------|--------|
| **Full DDL on RDS** | Apply remaining `DDL/` (+ alters) so all app routes match dev DB; smoke-test critical flows. |
| **Secrets & deploy hygiene** | `DATABASE_URL` / secrets via **Secrets Manager** + [`write-database-url.sh`](../deploy/ec2/write-database-url.sh); repeatable `pip install -r requirements.txt` on deploy. |
| **Git** | Resolve or abort incomplete **`git pull` / merge** so `main` is clean. |
| **Frontend URL** | Optional **HTTPS** for SPA (CloudFront + custom domain) vs HTTP S3 website; add new origin to **`CORS_ORIGINS`**. |
| **Infra follow-through** | ACM/443 on ALB if still desired; **S3** buckets for uploads/OCR; **SQS** if async; **IAM** least-privilege; **CloudWatch** alarms; ASG/consumer strategy. |

**Velocity anchor (~12 hours of paired work, 2026-04-17)**

In about **12 hours** of focused collaboration we went from confusing **“port 8000”** / CORS symptoms to a **working production login**: traced **wrong DB name** → missing **`login_ref`** → **`argon2-cffi`** → **`dealer_ref`/`oem_ref`** → clarified **500 vs CORS** → **`VITE_API_URL`** build + **S3 sync** + **CloudFront invalidation**, and wrote **[`troubleshooting-8000-error.md`](troubleshooting-8000-error.md)** with the full command playbook. Use that as a **rough pace**: one similar session clears **one major cluster** of issues (DB + runtime + edge + docs).

**Rough time to finish pending migration tasks (hours)**

Assumptions: **one engineer** who knows the repo; **solo** wall-clock unless noted. Ranges are **billable / focused hours**, not calendar weeks.

| Bucket | Hours (range) | Notes |
|--------|----------------|--------|
| **DDL + data parity + smoke tests** | **8–24 h** | Many `DDL/` files + alters; faster if you batch-apply and test only critical paths; slower if you migrate row-level data from dev. |
| **Secrets Manager + scripted EC2 deploy** | **4–8 h** | Wire `write-database-url.sh`, IAM, document one-command deploy. |
| **HTTPS SPA + DNS + CORS update** | **4–8 h** | CloudFront for static site + Route 53 alias + add origin to `CORS_ORIGINS`. |
| **S3 uploads / app buckets + IAM tighten + basic CloudWatch** | **8–16 h** | Depends how many buckets/policies and which alarms. |
| **SQS / async / scale-out** | **24–80+ h** | Only if in scope; product-dependent. |
| **Git merge conflicts + cleanup** | **2–6 h** | If many files conflicted. |

**Order-of-magnitude total** to reach “migration solid for real traffic” **excluding** big SQS/async work: about **24–48 h** of focused work (≈ **2–4** sessions like the 12 h one), or **double** that if DDL testing is very thorough or data migration is heavy.

**Client-side app development (SPA) — hours**

- **Migration-only client work left:** mostly optional (**HTTPS** for SPA, small UX). **~4–8 h** if you add CloudFront + domain for the static app only.
- **Ongoing product work** (new flows, pages, integrations) — same codebase, different scope:

| Size | Hours (rough) |
|------|----------------|
| Small fix / tweak | **2–8 h** |
| Medium feature (one workflow end-to-end) | **16–40 h** |
| Large module (major area + API + tests) | **80–200+ h** |

Define a **short backlog** (3–5 items) to replace ranges with a single estimate.

**Calendar conversion:** 12 h of paired work ≈ **1.5–2** full solo days at the same intensity, or **~3–4** half-days spread across a week.

---

## Where we left off

- **Terraform `terraform/network/`** has been applied; core networking, RDS PostgreSQL **16.13**, public ALB, private ASG + launch template, IAM instance profile, and bootstrap **Nginx** (`/health` → 200, `/` → 503) are in place.
- **ALB health check verified:** HTTP **200** from the ALB DNS on `/health` (use PowerShell or `curl.exe` — see below).
- **Canonical design + snapshot:** [`Production_cloud_design.md`](Production_cloud_design.md) (**§7** as-built, **§8** versioning — e.g. v0.2) — §7 summarizes Terraform/runtime decisions; for live IDs/ARNs/DNS use `terraform output` (not the doc); **do not** paste secrets; Secrets Manager for `rds_master_user_secret_arn`.
- **Remote state:** S3 bucket `saathi-tfstate-261399254938` + DynamoDB lock table `terraform-locks` (lock table is **only** for Terraform state, not the app database).

---

## Quick verify (next session)

**PowerShell (not Unix `curl`):**

```powershell
(Invoke-WebRequest -Uri "http://saathi-pub-alb-1280951205.ap-south-1.elb.amazonaws.com/health" -UseBasicParsing).StatusCode
```

**Or real curl:**

```powershell
curl.exe -sS -o NUL -w "%{http_code}`n" http://saathi-pub-alb-1280951205.ap-south-1.elb.amazonaws.com/health
```

**Refresh outputs after any apply:**

```powershell
cd "C:\Users\arya_\OneDrive\Desktop\My Auto.AI\terraform\network"
terraform output
```

---

## Planned next work (priority order — adjust as needed)

1. **App on EC2:** Follow **[`deploy/ec2/README.md`](../deploy/ec2/README.md)** — Gunicorn + `uvicorn.workers.UvicornWorker` (4 workers, timeout 60s), Nginx proxy, **systemd**, **`write-database-url.sh`** for **`DATABASE_URL`** from the RDS master secret; merge **`dotenv.production.example`** and required portal URLs + **`JWT_SECRET`**. Then remove bootstrap **`saathi-health.conf`** and switch to **`nginx-saathi.conf`**.
2. **HTTPS on ALB:** ACM certificate (same region as ALB: `ap-south-1`), ALB listener **443**, optional HTTP→HTTPS redirect; security groups already allow 443 when ready.
3. **Edge:** **CloudFront** in front of ALB; **WAF** Web ACL association (note: CloudFront uses WAF in **us-east-1** for global resources — confirm current AWS behavior when implementing).
4. **DNS:** **Route 53** for `dealersaathi.co.in` (or chosen hostname) → CloudFront or ALB alias as designed.
5. **Data / async (Terraform):** **S3** buckets (app assets, uploads, etc.), **SQS** (+ DLQ if required); then **tighten IAM** on `saathi-ec2-app-role` from the current broad policy to least privilege.
6. **Ops:** CloudWatch alarms, optional RDS Proxy / Performance Insights later; **ASG max = 2:** decide **single active consumer** / idempotency for watchers or SQS before relying on scale-out.

---

## Files to open first tomorrow


| Item                    | Path                                                                     |
| ----------------------- | ------------------------------------------------------------------------ |
| Cloud design + snapshot | `[Documentation/Production_cloud_design.md](Production_cloud_design.md)` |
| EC2 app deploy (Gunicorn/Nginx/systemd) | [`deploy/ec2/README.md`](../deploy/ec2/README.md) |
| Terraform root module   | `[terraform/network/](../terraform/network/)`                            |
| This handoff            | `[Documentation/session_resume.md](session_resume.md)`                   |


---

## Cursor / plan reference

- Workspace: `C:\Users\arya_\OneDrive\Desktop\My Auto.AI`
- Related plan name mentioned in `Production_cloud_design.md`: `beta_aws_production_plan_ad76cfe7` (IaC todos and phased rollout — search in Cursor plans if still available).

If IDs or DNS change after `terraform destroy` / recreate, **refresh §7** from `terraform output` and update this file’s ALB URL in “Quick verify” if needed.