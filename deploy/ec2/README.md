# EC2 app deployment (FastAPI + Gunicorn + Nginx)

Instances run **Amazon Linux 2023**. The launch template **user_data** now **self-deploys** the full stack (system packages → git clone → Python 3.11 venv → pip → Nginx proxy → Gunicorn systemd service). New instances launched by the ASG come up healthy without manual SSH.

**Ongoing deploys:** follow **[DEPLOY.md](./DEPLOY.md)** (git pull → pip → restart).

## Layout on the server

| Path | Purpose |
|------|---------|
| `/opt/saathi/` | Git clone of the monorepo root |
| `/opt/saathi/backend/venv/` | Python 3.11 virtualenv |
| `/opt/saathi/venv` | Symlink → `backend/venv` (used by systemd paths) |
| `/opt/saathi/backend/.env` | Secrets and environment (not in git) |

## How self-deploy works (user_data)

The Terraform `app_user_data` in `asg.tf` runs on every new instance:

1. Installs `nginx`, `git`, `python3.11`, `gcc`, `pkg-config`, `cairo-devel`, CloudWatch Agent
2. Configures and starts CloudWatch Agent (mem + disk metrics)
3. Starts Nginx with a stub `/health` → `200` (so the ALB marks the target healthy immediately)
4. Clones the repo to `/opt/saathi` (uses `app_git_repo_url`; optional GitHub PAT from SSM via `app_github_pat_ssm_param`)
5. Creates a Python 3.11 venv at `/opt/saathi/backend/venv`, symlinks `/opt/saathi/venv`
6. Runs `pip install -r requirements.txt`
7. Optionally writes `/opt/saathi/backend/.env` from an SSM SecureString parameter (`app_dotenv_ssm_param`)
8. Replaces the Nginx stub with `deploy/ec2/nginx-saathi.conf` (proxy to `127.0.0.1:8000`)
9. Installs + starts `saathi-api.service` (Gunicorn via `run-gunicorn.sh`)
10. Polls `/health` until the app responds (up to 60s)

### Terraform variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `app_git_repo_url` | `https://github.com/AutoBot80/MyAuto` | Repo HTTPS URL |
| `app_git_branch` | `main` | Branch to check out |
| `app_github_pat_ssm_param` | `""` | SSM param name for GitHub PAT (private repos) |
| `app_dotenv_ssm_param` | `""` | SSM param name for `.env` file content |

### Storing `.env` in SSM (recommended)

```bash
aws ssm put-parameter \
  --name "/saathi/production/dotenv" \
  --type SecureString \
  --value "$(cat /opt/saathi/backend/.env)" \
  --region ap-south-1
```

Then set `app_dotenv_ssm_param = "/saathi/production/dotenv"` in `terraform.tfvars`.

## Manual setup (existing instance)

If you need to set up Nginx on an instance that was launched before the self-deploy user_data:

```bash
sudo dnf install -y nginx
sudo rm -f /etc/nginx/conf.d/saathi-health.conf /etc/nginx/conf.d/default.conf
sudo cp /opt/saathi/deploy/ec2/nginx-saathi.conf /etc/nginx/conf.d/saathi.conf
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl reload nginx
```

## Verify

- Local: `curl -sS http://127.0.0.1:8000/health`
- Via Nginx: `curl -sS http://127.0.0.1/health`
- Via ALB (from your PC): use the ALB DNS or CloudFront URL.

Target group health should remain **healthy** (`/health` → HTTP 200).

## Rolling updates

See **[DEPLOY.md](./DEPLOY.md)** for the full checklist (dependencies, `DATABASE_URL` refresh, `JWT_SECRET`, health check).

```bash
sudo systemctl restart saathi-api
```

If you change the **launch template** user-data, trigger an ASG instance refresh:

```bash
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name saathi-asg-app \
  --region ap-south-1
```
