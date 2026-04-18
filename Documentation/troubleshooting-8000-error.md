# Troubleshooting the "port 8000" / "Cannot connect to backend" error

**Updated:** 2026-04-17

The red error message that mentions **port 8000**, `daily_startup.bat`, or `localhost:5173` is a **generic client-side fallback** from `client/src/api/client.ts`. It fires whenever `fetch()` fails **for any reason** — not just a missing local backend.

In production, three completely different root causes produce the **same** user-visible message. **Do not assume CORS** until you have eliminated the first two.

---

## Decision tree

```
Browser shows "port 8000" error
│
├─ 1. Is the JS bundle calling the right API host?
│     DevTools → Network → find the failed request → Request URL
│     Expected: https://api.dealersaathi.co.in/auth/login
│     If wrong (empty, localhost, S3 host) → VITE_API_URL missing → rebuild
│
├─ 2. Does the API return a non-500 response?
│     curl -s -D - -X POST https://api.dealersaathi.co.in/auth/login \
│       -H "Content-Type: application/json" \
│       -H "Origin: http://dealersaathi-prod-1980.s3-website.ap-south-1.amazonaws.com" \
│       -d '{"login_id":"shashank","password":"test"}'
│     If HTTP 500 → backend crash → check journalctl (step 3)
│     If HTTP 401/403 with JSON body → API works, password/role issue
│     If HTTP 200 with token → API works, problem is elsewhere
│
└─ 3. What crashed the backend?
      sudo journalctl -u saathi-api -n 80 --no-pager | grep -i -E 'error|fatal|exception'
      Common causes (in order we hit them):
        a. database "..." does not exist       → wrong DB name in DATABASE_URL
        b. relation "login_ref" does not exist  → DDL not applied
        c. relation "dealer_ref" does not exist → DDL not applied
        d. MissingBackendError: argon2          → pip install argon2-cffi
        e. password hash corrupted              → shell $-expansion mangled the hash
```

---

## Why a 500 looks like CORS

FastAPI's `CORSMiddleware` adds `access-control-allow-origin` **only when the handler succeeds or raises an `HTTPException`**. An unhandled crash (500) bypasses the middleware, so the response has **no CORS headers**. The browser then reports:

> `Access to fetch ... has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header`

This is **not** a CORS configuration problem. The fix is to **resolve the 500**, not to change `CORS_ORIGINS`.

**How to tell the difference:**
- Run the `curl` test from step 2 above (from EC2 or any machine that can reach the API).
- If `curl` returns **500** → backend crash, not CORS.
- If `curl` returns **200/401/403** but browser still blocked → actual CORS misconfiguration.

---

## Root cause details

### A. `VITE_API_URL` not baked into the build

`client/.env.production` (tracked in git) sets:

```env
VITE_API_URL=https://api.dealersaathi.co.in
```

Vite inlines this at **build time**. If the file is missing or empty, `baseUrl` becomes `""` and `fetch` calls the **current page host** (the S3 bucket), which returns HTML instead of JSON.

**Fix:** Ensure `client/.env.production` exists → `npm run build` → `aws s3 sync` → invalidate CloudFront if applicable.

**Verify:** `Select-String -Path "client\dist\assets\*.js" -Pattern "api.dealersaathi"` — must match.

### B. Missing database / tables

The RDS instance identifier (e.g. `saathi-postgres`) is **not** the database name. The actual Postgres database name is set by Terraform's `rds_db_name` variable (default: `saathi`).

`DATABASE_URL` in `/opt/saathi/backend/.env` must end with the **correct database name**:

```
postgresql://user:pass@host:5432/saathi       ← correct
postgresql://user:pass@host:5432/saathi-postgres  ← wrong (instance identifier)
```

DDL scripts in `DDL/` must be applied to create tables. Login requires at minimum:
1. `04a_oem_ref.sql` → `oem_ref`
2. `04b_dealer_ref.sql` → `dealer_ref`
3. `25_roles_ref.sql` → `roles_ref`
4. `26_login_ref.sql` → `login_ref`
5. `27_login_roles_ref.sql` → `login_roles_ref`

Plus seed data in each (roles, users with hashed passwords, dealer rows, role assignments).

### C. Missing `argon2-cffi`

If password hashes are argon2 format (`$argon2id$...`), the `argon2-cffi` package must be installed:

```bash
source /opt/saathi/venv/bin/activate
pip install argon2-cffi
sudo systemctl restart saathi-api
```

Already added to `backend/requirements.txt`.

### D. Password hash corrupted by shell `$` expansion

Bcrypt hashes (`$2b$12$...`) and argon2 hashes (`$argon2id$...`) contain `$` characters. Pasting them into bash via `-c "..."` or `echo` can silently mangle them.

**Safe methods:**
- Use `psql` interactively and paste with **dollar-quoting**: `UPDATE login_ref SET pwd_hash = $pwd$HASH_HERE$pwd$ WHERE login_id = 'user';`
- Put the SQL in a `.sql` file and run `psql "$DATABASE_URL" -f file.sql`
- Generate the hash on EC2 directly: `python3 -c "from passlib.context import CryptContext; c=CryptContext(schemes=['bcrypt','argon2'], deprecated='auto'); print(c.hash('password'))"`

**Verify a hash works before restarting:**

```bash
cd /opt/saathi/backend && source /opt/saathi/venv/bin/activate
python3 -c "
from app.security.passwords import verify_password
print(verify_password('YOUR_PASSWORD', 'HASH_FROM_DB'))
"
```

### E. `.env` parse errors

Values with spaces (e.g. paths like `/opt/saathi/Uploaded scans`) must be **quoted** in `.env`:

```env
SOME_DIR="/opt/saathi/Uploaded scans"
```

`systemd`'s `EnvironmentFile=` parser handles this, but `source .env` in bash does not unless values are quoted.

---

## Quick diagnostic sequence (copy-paste)

```bash
# 1. Is the API running?
sudo systemctl status saathi-api --no-pager

# 2. Can it respond at all?
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health

# 3. Test login locally (bypasses CloudFront/CORS)
curl -s -D - -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login_id":"shashank","password":"test"}'

# 4. Test login through CloudFront with Origin header
curl -s -D - -X POST https://api.dealersaathi.co.in/auth/login \
  -H "Content-Type: application/json" \
  -H "Origin: http://dealersaathi-prod-1980.s3-website.ap-south-1.amazonaws.com" \
  -d '{"login_id":"shashank","password":"test"}'

# 5. Check latest crash
sudo journalctl -u saathi-api -n 80 --no-pager | grep -i -E 'error|fatal|exception'
```

---

## Full debug playbook (commands we ran 2026-04-17)

### Connect to EC2

No public IP — use SSM Session Manager:

```powershell
# From Windows (AWS CLI)
aws ssm start-session --target i-016450db1735bba18
```

### Load DATABASE_URL safely (avoids .env source errors from unquoted spaces)

```bash
export DATABASE_URL="$(cd /opt/saathi/backend && /opt/saathi/venv/bin/python3 -c \
  "from pathlib import Path; from dotenv import dotenv_values; print(dotenv_values(Path('.env'))['DATABASE_URL'])")"
```

### Check what DB name the app sees

```bash
cd /opt/saathi/backend && source /opt/saathi/venv/bin/activate
python3 <<'PY'
from pathlib import Path
from dotenv import load_dotenv
import os
load_dotenv(Path("/opt/saathi/backend/.env"))
url = os.environ.get("DATABASE_URL", "")
print("DB name suffix:", url.rsplit("/", 1)[-1] if url else "(empty)")
PY
```

### Check systemd is using the right .env

```bash
sudo systemctl show saathi-api -p EnvironmentFiles -p FragmentPath -p DropInPaths
sudo systemctl cat saathi-api
```

### Restart the API after .env changes

```bash
sudo systemctl daemon-reload
sudo systemctl restart saathi-api
sudo systemctl status saathi-api --no-pager
```

### Check latest errors

```bash
# Last 80 lines
sudo journalctl -u saathi-api -n 80 --no-pager

# Filter for errors only
sudo journalctl -u saathi-api -n 200 --no-pager | grep -i -E 'error|fatal|exception|traceback'

# Follow live (Ctrl+C to stop)
sudo journalctl -u saathi-api -f
```

### Test login locally (bypasses CloudFront and CORS)

```bash
curl -s -D - -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"login_id":"shashank","password":"YOUR_PASSWORD"}'
```

### Test login through CloudFront with Origin header

```bash
curl -s -D - -X POST https://api.dealersaathi.co.in/auth/login \
  -H "Content-Type: application/json" \
  -H "Origin: http://dealersaathi-prod-1980.s3-website.ap-south-1.amazonaws.com" \
  -d '{"login_id":"shashank","password":"YOUR_PASSWORD"}'
```

### Test CORS preflight

```bash
curl -s -D - -o /dev/null -X OPTIONS \
  -H "Origin: http://dealersaathi-prod-1980.s3-website.ap-south-1.amazonaws.com" \
  -H "Access-Control-Request-Method: POST" \
  https://api.dealersaathi.co.in/auth/login
```

### Install psql (Amazon Linux 2023)

```bash
sudo dnf install -y postgresql15
```

### Connect to RDS via psql

```bash
psql "$DATABASE_URL"
```

### Check which tables exist

```sql
\dt
```

### Check which tables are missing (login flow needs these)

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('oem_ref','dealer_ref','roles_ref','login_ref','login_roles_ref')
ORDER BY table_name;
```

### Copy DDL files to EC2 via S3 (no SSH needed)

```powershell
# From Windows
aws s3 sync "C:\Users\arya_\OneDrive\Desktop\My Auto.AI\DDL" s3://dealersaathi-prod-1980/DDL/
```

```bash
# On EC2
aws s3 sync s3://dealersaathi-prod-1980/DDL/ /tmp/DDL/
```

### Apply DDL in dependency order

```bash
for f in \
  04a_oem_ref.sql \
  04b_dealer_ref.sql \
  25_roles_ref.sql \
  26_login_ref.sql \
  27_login_roles_ref.sql; do
  echo "--- $f ---"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "/tmp/DDL/$f" || echo "FAILED: $f"
done
```

### Seed reference data (interactive psql)

```bash
psql "$DATABASE_URL"
```

Then paste SQL. Use **dollar-quoting** for values containing `$` (like password hashes):

```sql
-- OEM
INSERT INTO oem_ref (oem_id, oem_name, vehicles_type, dms_link)
VALUES (1, 'Hero MotoCorp Limited', '2W', 'http://127.0.0.1:8000/dummy-dms/')
ON CONFLICT (oem_id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('oem_ref','oem_id'),
  (SELECT COALESCE(MAX(oem_id),1) FROM oem_ref));

-- Dealers
INSERT INTO dealer_ref (dealer_id, dealer_name, address, pin, city, state, parent_id, phone, oem_id, auto_sms_reminders, rto_name, prefer_insurer, hero_cpi)
VALUES
  (100001, 'Arya Agencies', 'Bharatpur, Rajasthan', '321001', 'Bharatpur', 'Rajasthan', NULL, '9413112499', 1, 'Y', 'RTO-Bharatpur', 'National Insurance Company', 'N');
SELECT setval(pg_get_serial_sequence('dealer_ref','dealer_id'),
  (SELECT COALESCE(MAX(dealer_id),1) FROM dealer_ref));

-- Roles
INSERT INTO roles_ref (role_id, role_name, pos_flag, rto_flag, service_flag, admin_flag, dealer_flag)
VALUES
  (1, 'OWNER',  'Y', 'Y', 'Y', 'Y', 'Y'),
  (2, 'POS',    'Y', 'N', 'N', 'N', 'N'),
  (3, 'RTO',    'N', 'Y', 'N', 'N', 'N'),
  (4, 'MGR',    'Y', 'Y', 'Y', 'N', 'N'),
  (5, 'DEALER', 'Y', 'Y', 'Y', 'N', 'Y');
SELECT setval(pg_get_serial_sequence('roles_ref','role_id'),
  (SELECT COALESCE(MAX(role_id),1) FROM roles_ref));

-- Users (dollar-quote the hash to avoid $-expansion)
INSERT INTO login_ref (login_id, pwd_hash, name, phone, email, active_flag)
VALUES ('shashank', $pwd$PUT_HASH_HERE$pwd$, 'Shashank Arya', '9560393610', 'shashank@dealersaathi.com', 'Y');

-- Role assignments
INSERT INTO login_roles_ref (login_roles_ref_id, login_id, role_id, dealer_id)
VALUES (1, 'shashank', 1, 100001);
SELECT setval(pg_get_serial_sequence('login_roles_ref','login_roles_ref_id'),
  (SELECT COALESCE(MAX(login_roles_ref_id),1) FROM login_roles_ref));
```

### Generate a password hash on EC2

```bash
source /opt/saathi/venv/bin/activate
python3 -c "from passlib.context import CryptContext; c=CryptContext(schemes=['bcrypt','argon2'], deprecated='auto'); print(c.hash('YOUR_PASSWORD'))"
```

### Verify a hash works (before restarting)

```bash
cd /opt/saathi/backend && source /opt/saathi/venv/bin/activate
python3 <<'PY'
import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2, psycopg2.extras
from app.security.passwords import verify_password
load_dotenv(Path("/opt/saathi/backend/.env"))
conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()
cur.execute("SELECT pwd_hash FROM login_ref WHERE login_id = %s", ("shashank",))
h = cur.fetchone()["pwd_hash"]
print("hash prefix:", h[:20])
print("verify:", verify_password("YOUR_PASSWORD", h))
conn.close()
PY
```

### Install missing Python packages

```bash
source /opt/saathi/venv/bin/activate
pip install argon2-cffi
sudo systemctl restart saathi-api
```

### Frontend rebuild and deploy (from Windows)

```powershell
cd "C:\Users\arya_\OneDrive\Desktop\My Auto.AI\client"
npm run build

# Verify API URL is in the bundle
Select-String -Path "dist\assets\*.js" -Pattern "api.dealersaathi"

# Upload and invalidate
aws s3 sync dist/ s3://dealersaathi-prod-1980/ --delete
aws cloudfront create-invalidation --distribution-id E3FYMUCW328MPO --paths "/*"
```

### Check CORS_ORIGINS on EC2

```bash
grep CORS_ORIGINS /opt/saathi/backend/.env
```

Must include the **exact** origin the browser loads the SPA from (scheme + host, no trailing slash):

```
CORS_ORIGINS=http://dealersaathi-prod-1980.s3-website.ap-south-1.amazonaws.com
```

---

## Key identifiers

| Item | Value |
|------|-------|
| S3 bucket (frontend) | `dealersaathi-prod-1980` |
| S3 website URL | `http://dealersaathi-prod-1980.s3-website.ap-south-1.amazonaws.com` |
| CloudFront distribution (API) | `E3FYMUCW328MPO` |
| API domain | `https://api.dealersaathi.co.in` |
| EC2 instance | `i-016450db1735bba18` (private `10.0.11.76`, SSM only) |
| App env file | `/opt/saathi/backend/.env` |
| Systemd unit | `saathi-api` |
| DDL folder | `DDL/` (repo) or `/tmp/DDL/` (EC2 after S3 sync) |
