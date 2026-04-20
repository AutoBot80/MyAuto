# Electron Desktop App — Design, BRD, HLD, LLD

## Auto Dealer Management System (Dealer Saathi)

**Version:** 0.1  
**Last Updated:** April 2026

---

## 1. Executive Summary

The Electron desktop app is a **thin dealer-side shell** around the existing React client (`client/`). Business APIs, authentication, and PostgreSQL remain on **AWS** (FastAPI on EC2). The desktop package adds:

- **Local Playwright automation** via a **job-based Python sidecar** (one process per job, JSON on stdin/stdout, no HTTP server).
- **Silent printing** with fallback to the system print dialog.
- **Sandboxed file operations** under `D:\Saathi\`.
- **Safe auto-updates** (silent download; install only when the user restarts or explicitly chooses “install”).
- **Local logging** for Electron (`app.log`) and the sidecar (`sidecar.log`).

The web client continues to work in the browser unchanged; Electron is detected via `window.electronAPI` (see `client/src/electron.ts`).

---

## 2. Business Requirements (BRD)

### 2.1 Stakeholders

- Dealers (daily operators).
- Administrators and support (logs, updates).
- Engineering (build, release, monitoring).

### 2.2 Business Rules (BR-E)

| ID | Rule |
|----|------|
| BR-E1 | Primary API and database access use the **remote AWS** stack; no local PostgreSQL requirement for the Electron shell. |
| BR-E2 | Playwright automations run **on the dealer PC**, attaching to **local Chrome/Edge** (CDP), using a **short-lived Python process per job**. |
| BR-E3 | **Silent print** is preferred; if it fails, the app may fall back to a **preview/system print** flow. |
| BR-E4 | Local file and log paths stay under **`D:\Saathi\`** (configurable via `SAATHI_BASE_DIR`). |
| BR-E5 | Updates **download** automatically; **installation** does not run silently without user awareness (see §4.8). |
| BR-E6 | The React SPA must remain usable **without Electron** (browser deployment). |
| BR-E7 | No long-lived sidecar server: **one process per job** to limit memory leaks and stuck sessions. |
| BR-E8 | **Structured local logs** for support; optional future upload to AWS (out of scope for v0.1 implementation). |

### 2.3 Functional Requirements (FR-E)

| ID | Requirement |
|----|-------------|
| FR-E1 | Load the built React app (`client/dist`) in a `BrowserWindow`; production API URL via `VITE_API_URL` at client build time. |
| FR-E2 | Run sidecar jobs with **JSON stdin** and **JSON stdout**; exit code reflects success/failure. |
| FR-E3 | Enforce **per-job timeout** (default 120s; **900s** for `fill_dms` unless overridden by `timeoutMs`). Kill the process tree on timeout (Windows: `taskkill /F /T /PID`). |
| FR-E4 | Expose **IPC** for: sidecar jobs, printers, files, updater install/check. |
| FR-E5 | **getPrinters**, **print HTML**, **testPrint**; silent print with preview fallback. |
| FR-E6 | File APIs: list, move, exists, open folder, select folder — all **path-checked** to stay under the Saathi base directory. |
| FR-E7 | Updater: **autoDownload**; **not** auto-install on quit by default; user **install** IPC applies update (restart). |
| FR-E8 | **NSIS**-style installer targeting **`D:\Saathi`** (operator may change directory in the wizard). |
| FR-E9 | **`npm run build:all`** in `electron/`: build client → PyInstaller sidecar → `electron-builder`. |
| FR-E10 | Logs: `D:\Saathi\logs\app.log`, `D:\Saathi\logs\sidecar.log`. |

### 2.4 Non-Functional Requirements (NFR-E)

| ID | Requirement |
|----|-------------|
| NFR-E1 | Sidecar **must not** rely on system Python in production; ship **`job_runner.exe`** via PyInstaller. |
| NFR-E2 | No orphan automation processes on app quit; **kill tracked PIDs** on `will-quit`. |
| NFR-E3 | **Deterministic** release build: one command chain from clean git state. |
| NFR-E4 | **Security:** `contextIsolation`, no `nodeIntegration` in the renderer; path sandbox in main process. |

---

## 3. High-Level Design (HLD)

### 3.1 Logical architecture (ASCII)

```
+------------------+     HTTPS      +-------------------+
|  React (renderer)| ------------> |  AWS FastAPI      |
|  (client/dist)   |               |  RDS, S3, ...     |
+------------------+               +-------------------+
         | IPC
         v
+------------------+
| Electron main    |
|  - sidecar spawn |
|  - print / files |
|  - updater       |
|  - logger        |
+------------------+
         | spawn (stdin/stdout)
         v
+------------------+     CDP        +------------------+
| job_runner.exe   | ----------->  | Chrome / Edge    |
| (per-job process)|               | (dealer machine) |
+------------------+               +------------------+
```

### 3.2 Component inventory

| Component | Technology | Responsibility |
|-----------|------------|----------------|
| Renderer | React + Vite build | UI; calls AWS APIs with JWT (`client/src/api/client.ts`). |
| Preload | Electron preload | `contextBridge` → `window.electronAPI`. |
| Main | Node (Electron) | IPC, spawn sidecar, printing, files, updater, `app.log`. |
| Sidecar | Python + PyInstaller | `job_runner.py`: Playwright jobs, `sidecar.log`. |
| Updater | `electron-updater` | Download updates; install on user action (see code). |

### 3.3 Data flows

| Flow | Path |
|------|------|
| 3.3a API | Renderer → `fetch` → CloudFront/ALB → FastAPI. |
| 3.3b Automation | Renderer → IPC `sidecar:runJob` → main spawns `job_runner` → stdin JSON → Playwright → stdout JSON → IPC result. |
| 3.3c Print | Renderer → IPC `print:html` → hidden `BrowserWindow` → `webContents.print` (silent, then fallback). |
| 3.3d Files | Renderer → IPC `file:*` → `fs` after path resolution under base dir. |
| 3.3e Updates | `electron-updater` (packaged builds only) → events → renderer notifications → user `updater:install`. |

### 3.4 Configuration

| Variable | Purpose |
|----------|---------|
| `VITE_API_URL` | Set when **building** the React app; API base for production. |
| `SAATHI_BASE_DIR` | Optional; defaults to `D:\Saathi`. Sets `backend` paths when running automation (see `backend/app/config.py` `SAATHI_BASE_DIR`). |
| `SAATHI_SIDECAR_EXE` | Optional override path to `job_runner.exe`. |
| `SAATHI_PYTHON` | Dev only: Python executable for `job_runner.py`. |

### 3.4a CORS (API server; not an Electron-only setting)

CORS is configured in **`backend/app/main.py`** via Starlette **`CORSMiddleware`**. It applies to **the whole FastAPI app** (all URL paths): there is no separate “CORS path” per route.

| Mode | Behavior |
|------|----------|
| **`CORS_ORIGINS` unset** (typical local API) | Default list includes `http://localhost:5173` and `http://127.0.0.1:5173`, plus **`allow_origin_regex`** so LAN IPs (e.g. `http://192.168.x.x:5173`) work for Vite. |
| **`CORS_ORIGINS` set** (typical production) | **Only** the comma-separated origins in `CORS_ORIGINS` are allowed; **`allow_origin_regex` is `None`**. You must list **every** origin that loads the SPA: S3 website URL, CloudFront HTTPS URL, etc. If developers call the **production** API from Vite/Electron at `http://localhost:5173`, add **`http://localhost:5173`** and **`http://127.0.0.1:5173`** explicitly. |

Electron in dev loads the same URL as the browser (`http://localhost:5173`), so the **`Origin`** header matches Vite — no extra Electron-specific origin is required for that mode. Packaged apps loading **`file://`** may send `Origin: null` for some requests; if that breaks credentialed `fetch`, prefer loading the built UI from an **`https://`** origin (e.g. CloudFront) or adjust CORS/cookie policy as needed.

See also [`deploy/ec2/dotenv.production.example`](../deploy/ec2/dotenv.production.example) and [`deploy/frontend-s3-cloudfront.md`](../deploy/frontend-s3-cloudfront.md).

### 3.5 Repository layout

- `electron/` — Electron app, preload, IPC, `electron-builder.yml`.
- `electron/sidecar/job_runner.py` — Sidecar entry; `build_sidecar.py` — PyInstaller.
- `client/` — Unchanged contract for web; optional `isElectron()` branching later.

---

## 4. Low-Level Design (LLD)

### 4.1 Main process modules (`electron/src/main/`)

| File | Role |
|------|------|
| `index.ts` | App lifecycle, `BrowserWindow`, load `client-dist` (packaged) or `client/dist` / dev server. |
| `ipc-handlers.ts` | Registers `ipcMain.handle` channels. |
| `sidecar.ts` | `runSidecarJob`, timeout, stdout JSON parse, PID tracking, `killAllSidecarJobs`. |
| `printer.ts` | `getPrinters`, `printHtml`, `testPrint` (callback-based `webContents.print`). |
| `file-ops.ts` | Path resolution + `..` traversal checks under Saathi base. |
| `updater.ts` | `setupAutoUpdater` (skips if `!app.isPackaged`), `quitAndInstall`. |
| `logger.ts` | Append to `logs/app.log` with simple size rotation. |
| `paths.ts` | Saathi base, repo root, sidecar exe vs script path. |

### 4.2 IPC channels

| Channel | Handler | Notes |
|---------|---------|------|
| `sidecar:runJob` | `runSidecarJob(payload)` | Payload includes `type`, optional `saathi_base_dir`, `timeoutMs`, `params`. |
| `print:getPrinters` | `getPrinters` | |
| `print:html` | `printHtml` | |
| `print:test` | `testPrint` | |
| `file:list` | `listFiles` | |
| `file:move` | `moveFile` | |
| `file:exists` | `fileExists` | |
| `file:openFolder` | `openFolder` | |
| `file:selectFolder` | `selectFolder` | |
| `updater:install` | `quitAndInstall` | |
| `updater:check` | `autoUpdater.checkForUpdates` | |

Main → renderer events: `update:available`, `update:downloaded`, `update:error`.

### 4.3 Preload API (`window.electronAPI`)

See `electron/src/preload/index.ts` and `client/src/electron.ts` for the typed surface.

### 4.4 Python sidecar (`electron/sidecar/job_runner.py`)

- **Input:** one JSON object on stdin.
- **Output:** one JSON object on stdout; stderr used for logging.
- **Job types:** `ping` (no backend import); `fill_dms` → `run_fill_dms` from `app.services.fill_hero_dms_service`.
- **Env:** `SAATHI_BASE_DIR` must be set before importing `app.config` (handled in runner). Dealer secrets: `D:\Saathi\.env` and/or `backend/.env` via `python-dotenv`.

### 4.5 PyInstaller (`electron/sidecar/build_sidecar.py`)

- Builds `sidecar/dist/job_runner.exe` with `--onefile`, `--paths` to repo `backend`, and `--add-data` for `backend/app`.
- Requires: `pip install pyinstaller` and backend dependencies (`pip install -r backend/requirements.txt`).
- Playwright may require additional PyInstaller hooks for full parity; treat first build as integration smoke.

### 4.6 Build pipeline

Run from `electron/`:

```bash
npm install
npm run build:all
```

Steps:

1. **`npm run build:client`** — `cd ../client && npm run build`
2. **`npm run build:sidecar`** — `python build_sidecar.py`
3. **`npm run build:electron`** — `npm run build:ts && electron-builder --win`

**Local development:** run `npm run dev` from `electron/`. It compiles main/preload TypeScript, starts the Vite dev server (`client`, port 5173), waits until TCP port 5173 accepts connections (more reliable on Windows than probing `http://127.0.0.1`), then launches Electron loading `http://localhost:5173`. Loading `client/dist` via `file://` in dev is **not** used by default (Vite’s build uses absolute `/assets/...` URLs and would show a blank window). To force loading built files, set `SAATHI_USE_FILE_DIST=1` after configuring Vite `base` for relative assets.

**DevTools:** Chromium DevTools are **not** opened automatically. Set `SAATHI_OPEN_DEVTOOLS=1` when launching Electron (or in the environment used by `npm run dev`) to attach a detached DevTools window.

**API / “port 8000” errors:** With `VITE_API_URL` unset, the Vite dev app uses the dev-server proxy (`client/vite.config.ts`) and forwards API routes to **`http://127.0.0.1:8000`**. If FastAPI is not running locally, you will see connection failures to port 8000. Options: (1) start the backend: `python -m uvicorn app.main:app --reload --port 8000` from `backend/`; or (2) point the client at your deployed API by setting **`VITE_API_URL`** (e.g. in `client/.env.local`) to the HTTPS API origin. The server **`CORS_ORIGINS`** must include `http://localhost:5173` when using option (2). Password hashing (e.g. Argon2) is a **server** concern and does not change this wiring.

### 4.7 Installer

- `electron-builder.yml`: extra resources include `../client/dist` as `client-dist` and `sidecar/dist` as `sidecar`.
- Packaged app loads `process.resourcesPath/client-dist/index.html`.
- Sidecar executable: `process.resourcesPath/sidecar/job_runner.exe`.

### 4.8 Updater semantics

- `autoUpdater.autoDownload = true`
- `autoUpdater.autoInstallOnAppQuit = false`
- Updater **disabled** when `!app.isPackaged` (development).
- Production feed URL: configure `electron-builder` **publish** (e.g. GitHub Releases) when ready.

---

## 5. Related documents

| Document | Role |
|----------|------|
| [technical-architecture.md](technical-architecture.md) | Overall stack |
| [Production_cloud_design.md](Production_cloud_design.md) | AWS edge and CORS (Electron origin must be listed in `CORS_ORIGINS` if applicable) |
| [deploy/POST_ELECTRON_TODO.md](../deploy/POST_ELECTRON_TODO.md) | Post-release operational tasks |

---

## 6. Document control

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | April 2026 | Initial Electron design: job sidecar, IPC, logging, updater, build |
