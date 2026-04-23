# Production deploy runbook (EC2)

Use this after [README.md](./README.md) one-time setup. **Do not commit** `/opt/saathi/backend/.env` or secrets.

**Ops backlog (suggested order):** (1) RDS backups / restore drill — [../../Documentation/rds-backup-recovery.md](../../Documentation/rds-backup-recovery.md); (2) optional JWT in SSM — below; (3) CloudWatch alarms — `terraform/network/cloudwatch_alarms.tf` (+ optional `alarm_sns_topic_arn`); (4) CI — [../../.github/workflows/backend-ci.yml](../../.github/workflows/backend-ci.yml).

---

## Step 0 — Essential packages (every new instance)

Amazon Linux 2023 minimal AMIs may be missing common tools after an instance restart or fresh launch. Install them first:

```bash
sudo yum install -y nano htop postgresql15
```

---

## Step 0.5 — Restore `.env` from Secrets Manager (if missing)

**The `.env` file is not in git.** If the instance was replaced by the ASG (scale event, health check failure, instance refresh) and `app_dotenv_secret_arn` was not configured in Terraform, the new instance will have **no** `.env` and the backend will fail to start.

Check if `.env` exists and has content:

```bash
wc -l /opt/saathi/backend/.env 2>/dev/null || echo "FILE MISSING"
```

If the file is missing or empty, restore it:

- **Option A — Secrets Manager (recommended):**

  ```bash
  aws secretsmanager get-secret-value \
    --secret-id "saathi/production/dotenv" \
    --query SecretString \
    --output text \
    --region ap-south-1 | sudo tee /opt/saathi/backend/.env > /dev/null
  sudo chmod 600 /opt/saathi/backend/.env
  ```

- **Option B — use the helper script (same thing):**

  ```bash
  sudo DOTENV_SECRET_ARN="arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:saathi/production/dotenv-XXXXXX" \
    bash /opt/saathi/deploy/ec2/load-dotenv.sh
  ```

- **Option C — manual paste (last resort):**

  ```bash
  sudo nano /opt/saathi/backend/.env
  ```

  Paste the full `.env` contents from your local copy, save, and exit.

**Always verify** the file has all required keys before starting the service:

```bash
grep -cE '^(DATABASE_URL|JWT_SECRET|CORS_ORIGINS|INSURANCE_BASE_URL|DMS_BASE_URL|ENVIRONMENT)=' /opt/saathi/backend/.env
```

Expected count: **6** (one per key). If fewer, the file is incomplete — add the missing vars before proceeding.

> **Tip:** To prevent this on future instances, store `.env` in Secrets Manager and set
> `app_dotenv_secret_arn` in `terraform.tfvars`.
> The launch-template user_data will write it automatically on boot (see [README.md](./README.md)).

---

## Step 1 — Align with repo and `.env` template

1. On the server, ensure the app lives at `/opt/saathi` (full monorepo root).
2. Copy or merge [dotenv.production.example](./dotenv.production.example) into `/opt/saathi/backend/.env` if you are creating `.env` for the first time.
3. From your workstation (with Terraform state), get the RDS master secret ARN:

   ```bash
   terraform output -raw rds_master_user_secret_arn
   ```

   You will use this as `RDS_SECRET_ARN` in Step 2.

---

## Step 2 — `DATABASE_URL` from Secrets Manager

The EC2 **instance profile** must allow `secretsmanager:GetSecretValue` on that secret (see Terraform for the app role).

On the instance (Session Manager):

```bash
export RDS_SECRET_ARN="arn:aws:secretsmanager:ap-south-1:ACCOUNT:secret:rds!..."   # paste from terraform output
cd /opt/saathi/deploy/ec2
chmod +x write-database-url.sh
sudo -E ./write-database-url.sh
```

If the secret JSON has no `host`/`hostname`, set **`RDS_HOST`** or **`RDS_ENDPOINT`** (from `terraform output rds_endpoint`) and re-run:

```bash
export RDS_ENDPOINT="$(terraform output -raw rds_endpoint)"   # run on workstation; paste value on EC2 if needed
sudo -E ./write-database-url.sh
```

Confirm the line in `/opt/saathi/backend/.env`:

```bash
grep '^DATABASE_URL=' /opt/saathi/backend/.env
```

`write-database-url.sh` bakes in **`?sslmode=require`**, which RDS expects.

### Step 2a — Auto-sync (recommended, avoids manual re-runs on rotation)

When the RDS **master** password is managed in Secrets Manager (`manage_master_user_password` in Terraform), the password can rotate. To keep `DATABASE_URL` in `/opt/saathi/backend/.env` aligned without you editing the file by hand:

1. **New ASG instances (after you apply Terraform and merge this repo’s launch template):** `user_data` creates `/opt/saathi/etc/rds-url-sync.env` (using `terraform output -raw rds_master_user_secret_arn` and `rds_endpoint` / region / DB name), runs [sync-database-url.sh](./sync-database-url.sh) once, then enables **[saathi-rds-url-sync.timer](./saathi-rds-url-sync.timer)** (by default about **every 2 minutes**; edit `OnUnitActiveSec` in the unit to tune). The sync replaces only the `DATABASE_URL=` line; it restarts **`saathi-api` only** if the URL **changed** and the service was **already** running.
2. **Long-lived instances (manual one-time):** `git pull`, then (adjust ARNs/endpoint from `terraform output` on your machine):

   ```bash
   sudo install -d -m 700 /opt/saathi/etc
   sudo tee /opt/saathi/etc/rds-url-sync.env > /dev/null <<'EOF'
   RDS_SECRET_ARN=paste-from-terraform-output-rds_master_user_secret_arn
   RDS_ENDPOINT=paste-from-terraform-output-rds_endpoint
   AWS_DEFAULT_REGION=ap-south-1
   RDS_DB_NAME=saathi
   EOF
   sudo chmod 600 /opt/saathi/etc/rds-url-sync.env
   sudo chmod +x /opt/saathi/deploy/ec2/write-database-url.sh /opt/saathi/deploy/ec2/sync-database-url.sh
   sudo cp /opt/saathi/deploy/ec2/saathi-rds-url-sync.service /opt/saathi/deploy/ec2/saathi-rds-url-sync.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now saathi-rds-url-sync.timer
   sudo /opt/saathi/deploy/ec2/sync-database-url.sh
   ```

If your app `.env` is the **monolithic** Secret in `app_dotenv_secret_arn`, that file can still list an old `DATABASE_URL`; the timer’s sync **overrides** the live `DATABASE_URL=` on disk to match the **RDS master** secret, which is the one that actually rotates with RDS.

| Check | Command |
|--------|---------|
| Next timer runs | `systemctl list-timers saathi-rds-url-sync.timer` |
| Last sync logs | `journalctl -u saathi-rds-url-sync.service -n 50 --no-pager` |

**Password rotation in production (avoid DB outages):** The app reads `DATABASE_URL` when **Gunicorn** starts, so a **new** password is applied only after **`.env` is updated and `saathi-api` restarts**. The failure window is roughly **(time until the next successful sync) + (restart)**, and only while the **old** in-memory config no longer matches RDS. To keep that small:

- **Run the sync timer (Step 2a) on every app instance** — the default [saathi-rds-url-sync.timer](./saathi-rds-url-sync.timer) polls **every 2 minutes**; for stricter SLOs, set `OnUnitActiveSec=1m`, then `systemctl daemon-reload && systemctl restart saathi-rds-url-sync.timer` after deploying the file.
- **Prefer ≥2 app instances** behind the ALB so a **sync + restart** on one instance does not take down all capacity (health checks will drain/restore targets as usual if configured).
- **Alarms and checks:** e.g. ALB **5xx** or target **unhealthy**, and **failed** `saathi-rds-url-sync` runs: `journalctl -u saathi-rds-url-sync.service -p err` / CloudWatch from the instance if you ship those logs. Fix **IAM** (`GetSecretValue` on the RDS master secret) and **network to RDS** if sync fails.
- **Staging test:** In a non-prod account or maintenance window, trigger a **Secrets Manager** rotation (or a manual password update **consistent with** the secret) and watch **health** + sync logs; confirm the API recovers without manual SSH.
- **Optional, faster than polling:** An **EventBridge** rule on **Secrets Manager rotation** (or a narrow schedule) that runs **SSM `AWS-RunShellScript`** on the ASG to execute `sync-database-url.sh` immediately, so you are not limited by the timer. Polling is simpler; event-driven is best when you need sub-minute cutover.
- **Longer-term alternative:** Fetch the RDS secret **inside the application** (AWS SDK) when opening DB connections, so a password change does not require a process restart — a larger app change; the timer + restart pattern is the usual first step for Gunicorn-style apps.

---

## Step 3 — `JWT_SECRET` and required vars

1. Set **`JWT_SECRET`** to a long random string (≥32 characters). Generate once, store safely (password manager or team vault).

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Edit `/opt/saathi/backend/.env` and ensure at least:

   - `JWT_SECRET=...`
   - `CORS_ORIGINS=...` (comma-separated, no spaces around commas; include your S3 website origin and any app origins)
   - `DMS_BASE_URL`, `VAHAN_BASE_URL`, `INSURANCE_BASE_URL`
   - `ENVIRONMENT=production`
   - `AUTH_DISABLED=false`

3. **Optional — JWT in SSM (overrides `JWT_SECRET` in `.env` for Gunicorn):**

   - Terraform grants the EC2 role `ssm:GetParameter` on `/${project}/...` (see `terraform output -raw jwt_ssm_parameter_name_example`).
   - Create a **SecureString** parameter (once), e.g.  
     `aws ssm put-parameter --name "/saathi/production/jwt_secret" --type SecureString --value "$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")"`  
     (use the same path as `jwt_ssm_parameter_name_example` if you use the default `project_name`.)
   - In `/opt/saathi/backend/.env` add **one line**:  
     `JWT_SSM_PARAMETER_NAME=/saathi/production/jwt_secret`  
     (You can keep `JWT_SECRET=` in `.env` for emergencies; the wrapper loads SSM last and wins.)
   - Deploy [run-gunicorn.sh](./run-gunicorn.sh) + [inject_ssm_jwt.py](./inject_ssm_jwt.py), `chmod +x run-gunicorn.sh`, copy [saathi-api.service](./saathi-api.service) to `/etc/systemd/system/`, then `sudo systemctl daemon-reload && sudo systemctl restart saathi-api`.

---

## Step 4 — Deploy code and restart

After `git pull` (or rsync) updates `/opt/saathi`:

```bash
cd /opt/saathi
chmod +x deploy/ec2/run-gunicorn.sh
source /opt/saathi/backend/venv/bin/activate
pip install -r backend/requirements.txt
sudo cp deploy/ec2/saathi-api.service /etc/systemd/system/saathi-api.service   # first time or when the unit file changed
sudo systemctl daemon-reload
sudo systemctl restart saathi-api
curl -sS http://127.0.0.1:8000/health
```

The unit file includes **`ExecStartPre=chown … ec2-user`** on `/opt/saathi/backend/venv` so the venv stays writable by `ec2-user` after every restart (covers accidental `sudo pip install`). New ASG instances get the same ownership from Terraform `user_data` after the initial `pip install`.

If RDS credentials rotate or you changed the secret, re-run **Step 2** (or wait for the **Step 2a** timer, when enabled) before/instead of a manual `DATABASE_URL` edit, then `sudo systemctl restart saathi-api` if the timer is not in use.

---

## Quick verify

- `sudo systemctl status saathi-api --no-pager`
- `curl -sS http://127.0.0.1:8000/health`
- ALB / CloudFront targets healthy on `/health`

---

## Troubleshooting — instance refresh / ASG replacement

When the ASG replaces an instance (scale event, health-check failure, instance refresh),
the new instance boots from scratch via the launch-template `user_data`. Common failures:

| Symptom | Cause | Fix |
|---------|-------|-----|
| Backend crashes: `Missing required environment variables` | `.env` not written (Secrets Manager secret missing or `app_dotenv_secret_arn` not set) | Store `.env` in Secrets Manager — see **Step 0.5** above |
| `ModuleNotFoundError: argon2` (or any Python package) | `requirements.txt` was updated after the AMI was baked, or pip install failed silently | SSH in and run `source /opt/saathi/backend/venv/bin/activate && pip install -r /opt/saathi/backend/requirements.txt && sudo systemctl restart saathi-api` |
| `Permission denied` installing into `venv/.../site-packages` (e.g. `_argon2_cffi_bindings`) | Venv dirs owned by `root` from an earlier `sudo pip install` | `sudo chown -R "$(whoami):$(whoami)" /opt/saathi/backend/venv` then `pip install -r backend/requirements.txt` as that user, **or** `sudo /opt/saathi/backend/venv/bin/pip install -r /opt/saathi/backend/requirements.txt` once, then fix ownership |
| `passlib.exc.MissingBackendError: argon2: no backends available` | `argon2-cffi` not installed in the active venv (failed pip, wrong venv, or permissions) | `pip install 'argon2-cffi>=23.1.0'` in `/opt/saathi/backend/venv`, restart `saathi-api`. `requirements.txt` includes `passlib[bcrypt,argon2]` and `argon2-cffi`—always install from `backend/requirements.txt` after `git pull`. |
| `nano` / `htop` missing | Older launch template without those packages | Update launch template (`asg.tf` packages list) and trigger instance refresh |
| Nginx shows `conflicting server_name` warning | Stub health config not removed | `sudo rm /etc/nginx/conf.d/saathi-health.conf && sudo nginx -t && sudo systemctl reload nginx` |
| `saathi-api.service not found` | Service file not copied to systemd | `sudo cp /opt/saathi/deploy/ec2/saathi-api.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now saathi-api` |

### Storing `.env` in Secrets Manager (one-time, from workstation)

This is the **permanent fix** for `.env` disappearing on new instances. Run from your
local machine (PowerShell) where you have AWS admin credentials.

**First time — create the secret:**

```powershell
$envContent = Get-Content 'C:\Users\arya_\OneDrive\Desktop\Saathi Docs\Save - Prod .env' -Raw
aws secretsmanager create-secret `
  --name "saathi/production/dotenv" `
  --description "Full backend .env for production EC2 instances" `
  --secret-string $envContent `
  --region ap-south-1
```

Copy the **ARN** from the output and set it in `terraform.tfvars`:

```hcl
app_dotenv_secret_arn = "arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:saathi/production/dotenv-XXXXXX"
```

Then `terraform apply` to update the launch template. Future instances will pull `.env`
automatically during boot.

### Updating `.env` in Secrets Manager after changes

Whenever you change the production `.env` (new keys, rotated secrets), update the secret:

```powershell
$envContent = Get-Content 'C:\Users\arya_\OneDrive\Desktop\Saathi Docs\Save - Prod .env' -Raw
aws secretsmanager put-secret-value `
  --secret-id "saathi/production/dotenv" `
  --secret-string $envContent `
  --region ap-south-1
```

The next instance launch or refresh will pick up the new values. Existing running instances
keep their current `.env` until restarted — to update a live instance without replacing it:

```bash
sudo DOTENV_SECRET_ARN="arn:aws:secretsmanager:ap-south-1:ACCOUNT_ID:secret:saathi/production/dotenv-XXXXXX" \
  bash /opt/saathi/deploy/ec2/load-dotenv.sh
sudo systemctl restart saathi-api
```
