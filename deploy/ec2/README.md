# EC2 app deployment (FastAPI + Gunicorn + Nginx)

Use this after Terraform has created the VPC, ALB, ASG, and RDS. Instances run **Amazon Linux 2023** with a **bootstrap Nginx** that returns `200` on `/health` until you replace it with the configs here.

## Layout on the server

The backend expects the **repository root** layout (same as development): `backend/` plus sibling dirs such as `Uploaded scans/`, `ocr_output/`, etc. Recommended deploy root:

| Path | Purpose |
|------|---------|
| `/opt/saathi/` | Git clone or rsync of the full project (this monorepo root) |
| `/opt/saathi/venv/` | Python virtualenv |
| `/opt/saathi/backend/.env` | Secrets and environment (not in git) |

## One-time setup (SSM Session Manager)

1. Connect to an instance: **EC2 → Instances → Connect → Session Manager**.
2. Install OS packages and Python tooling:

```bash
sudo dnf install -y git nginx python3-pip
sudo mkdir -p /opt/saathi
```

3. Place application code under `/opt/saathi` (git clone with a deploy key, **rsync** from your laptop, or CI artifact upload to S3 + download). Create required directories:

```bash
sudo mkdir -p "/opt/saathi/Uploaded scans" /opt/saathi/ocr_output /opt/saathi/Challans \
  "/opt/saathi/Bulk Upload" /opt/saathi/.cache
sudo chown -R ec2-user:ec2-user /opt/saathi
```

4. Create the venv and install dependencies:

```bash
cd /opt/saathi
python3 -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r backend/requirements.txt
```

5. **RDS URL from Secrets Manager** (instance profile must allow `GetSecretValue` on the RDS master secret — already granted in Terraform for the managed secret):

```bash
# From your workstation: terraform output -raw rds_master_user_secret_arn
export RDS_SECRET_ARN="arn:aws:secretsmanager:ap-south-1:ACCOUNT:secret:rds!..."
cd /opt/saathi/deploy/ec2
sudo chmod +x write-database-url.sh
sudo -E ./write-database-url.sh
```

6. Merge `deploy/ec2/dotenv.production.example` into `/opt/saathi/backend/.env` and set at least **`JWT_SECRET`** (≥32 characters), **`CORS_ORIGINS`**, **`DMS_BASE_URL`**, **`VAHAN_BASE_URL`**, **`INSURANCE_BASE_URL`**, and **`ENVIRONMENT=production`**. `AUTH_DISABLED` must stay **`false`** in production.

## Gunicorn + systemd

```bash
sudo cp /opt/saathi/deploy/ec2/saathi-api.service /etc/systemd/system/saathi-api.service
sudo systemctl daemon-reload
sudo systemctl enable saathi-api
sudo systemctl start saathi-api
sudo systemctl status saathi-api
```

Gunicorn uses **`deploy/ec2/gunicorn.conf.py`**: 4 workers, **`uvicorn.workers.UvicornWorker`**, **60s** timeout.

## Nginx in front of Gunicorn

Replace the bootstrap health-only config with the full proxy:

```bash
sudo rm -f /etc/nginx/conf.d/saathi-health.conf
sudo cp /opt/saathi/deploy/ec2/nginx-saathi.conf /etc/nginx/conf.d/saathi.conf
sudo nginx -t
sudo systemctl reload nginx
```

Nginx proxies **`/`** to **`127.0.0.1:8000`** with **`proxy_read_timeout 60s`**, matching the ALB idle timeout and Gunicorn timeout.

## Verify

- Local: `curl -sS http://127.0.0.1:8000/health`
- Via ALB (from your PC): use `Invoke-WebRequest` or `curl.exe` against the ALB DNS (see `Documentation/session_resume.md`).

Target group health should remain **healthy** (`/health` → HTTP 200).

## Rolling updates

After changing code, reload the service:

```bash
sudo systemctl restart saathi-api
```

If you change the **launch template** user-data for fresh instances, create a new instance refresh or replace the ASG as per your release process.
