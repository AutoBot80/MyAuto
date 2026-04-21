# After Electron build — automation backlog

Do these when the Electron client build is in a stable place.

- [ ] **1. Script: push latest backend / DB-related changes** — e.g. `git pull`, `pip install`, `systemctl restart saathi-api`, optional `write-database-url.sh` if RDS secret changed.
- [ ] **2. Script: push client app updates** — S3 / CloudFront or your chosen static hosting path (align with `CORS_ORIGINS`).
- [ ] **3. Script or runbook: restart EC2 / recycle ASG instances** — instance refresh or targeted replace when user_data or AMI changes matter.
- [ ] **4. Health check daily at 08:00** — scheduled synthetic check (e.g. EventBridge → Lambda → `GET /health`, or external monitor; SNS on failure).

Terraform / SNS alarms are separate from this list.

---

## Minimal dealer PC `.env` (`D:\Saathi\.env` or `C:\Saathi\.env`)

The Electron sidecar delegates database operations to the cloud API.

**NSIS installer** (`electron/resources/build/installer.nsh`): on first install, if `.env` does not exist yet, it creates one with `DMS_MODE=real` and default **`DMS_BASE_URL`**, **`INSURANCE_BASE_URL`**, **`VAHAN_BASE_URL`** (Hero / production entry URLs). Upgrades do not overwrite an existing `.env`.

Optional Playwright flags (add manually if needed):

```env
DMS_PLAYWRIGHT_HEADED=1
```

**What is NOT needed on the dealer PC** (all handled by cloud API):
- `DATABASE_URL` — no DB credentials leave the server
- `JWT_SECRET` — sidecar authenticates with the operator's JWT from the logged-in session
- `DMS_LOGIN_USER` / `DMS_LOGIN_PASSWORD` — browser memory / cookies on the dealer PC
- Any site login credentials — handled by browser session on the dealer PC

**Site URLs:** The client prefers **`GET /settings/site-urls`** from the API when reachable. The local `.env` values above are used for **IPC fallback** (offline / misconfigured `VITE_API_URL`) and should stay aligned with the server `.env` when possible.

The sidecar receives `api_url` and `jwt` from the Electron client for every job,
so no API endpoint or auth configuration is stored on disk.
