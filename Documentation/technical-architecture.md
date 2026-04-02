# Technical Architecture
## Auto Dealer Management System

**Version:** 0.8  
**Last Updated:** April 2026

---

## 1. Technology Stack

| Layer | Technology | Notes |
|-------|------------|--------|
| Client | React (TypeScript), Vite | Light UI; basic validation only. |
| API | Python 3.x, FastAPI | REST API, CORS, auth (planned). |
| Database | PostgreSQL | RDS on AWS; local for dev. |
| Queue | Local in-process queue or AWS SQS | Current bulk processing supports local fallback and SQS-backed dispatch. |
| OCR | Tesseract, AWS Textract, Vision API | Tesseract for OCR/pre-OCR; Textract/Vision for document extraction. |
| Browser automation | Playwright (Python) | Headless browser for portal submission. |
| File storage | Local folders now, optional S3 later | `Uploaded scans/` for working/customer files; `ocr_output/` for OCR and automation artifacts. |
| Hosting (target) | AWS | VPC, ECS Fargate or similar, ALB, RDS, SQS, and optional S3. |

---

## 2. Architecture Diagram (Logical)

```
┌─────────────────────────────────────────────────────────────────┐
│                     Dealer Workstation(s)                        │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  React Client (Browser or Electron)                          │ │
│  │  - Header (dealership name, date)                            │ │
│  │  - Forms, lists, upload, job status                          │ │
│  └───────────────────────────┬─────────────────────────────────┘ │
└──────────────────────────────┼───────────────────────────────────┘
                               │ HTTPS
                               v
┌─────────────────────────────────────────────────────────────────┐
│                           AWS (Target)                           │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐  │
│  │ ALB /       │───>│  FastAPI     │───>│  PostgreSQL (RDS)   │  │
│  │ API Gateway │    │  (ECS)       │    │                    │  │
│  └─────────────┘    └──────┬───────┘    └────────────────────┘  │
│                            │                     ^              │
│                            │ enqueue             │ read/write    │
│                            v                     │              │
│  ┌─────────────┐    ┌──────┴───────┐    ┌────────┴────────────┐  │
│  │ Local / S3  │<---│ Local / SQS  │    │  OCR Worker (ECS)    │  │
│  │ artifacts   │    │ bulk queue   │--->│  - Tesseract         │  │
│  └─────────────┘    └──────┬───────┘    └─────────────────────┘  │
│                            │                                      │
│                            v                                      │
│                     ┌──────────────┐                              │
│                     │ Playwright   │                              │
│                     │ Worker (ECS) │                              │
│                     │ - Portals    │                              │
│                     └──────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Local Development Setup

| Service | How to run |
|---------|------------|
| PostgreSQL | Local install; DB `auto_ai`; credentials in `backend/.env`. |
| Backend | `uvicorn app.main:app --reload --port 8000` from `backend/` with venv. |
| Client | `npm run dev` in `client/` (e.g. port 5173). |
| Bulk worker | API-integrated loops or `python backend/run_bulk_worker.py` for a standalone worker. |
| Queue mode | Use local fallback by default, or configure SQS in `backend/.env`. |

### 3.1 External portal URLs (production)

Configure **`backend/.env`** (copy from **`backend/.env.example`**). The API validates **`DMS_BASE_URL`**, **`VAHAN_BASE_URL`**, and **`INSURANCE_BASE_URL`** at startup.

| Variable | Role |
|----------|------|
| **`DMS_BASE_URL`** | Hero Connect / Siebel entry URL (same host you open in Edge after login). |
| **`DMS_MODE`** | Default **`real`** (aliases: `siebel`, `live`, `production`, `hero`). **`dummy`** is rejected. |
| **`DMS_REAL_URL_CONTACT`** | Full **GotoView** URL for Contact Find (required for Fill DMS). Optional **`DMS_REAL_URL_*`** for other screens — see **LLD §2.4b** / **`.env.example`**. |
| **`VAHAN_BASE_URL`** | Production VAHAN portal base; automated fill/pay in-repo are stubbed until implemented. |
| **`INSURANCE_BASE_URL`** | Insurer portal root opened by Playwright (e.g. Hero MISP). |

**`GET /settings/site-urls`** exposes bases and **`dms_mode`** to the client (no secrets).

---

## 4. Security (Target)

- **Authentication:** JWT or OAuth2; refresh tokens; no secrets in client code.
- **Authorization:** All APIs scoped by `dealer_id` (tenant); RBAC for admin.
- **Secrets:** AWS Secrets Manager or SSM for DB, AWS, and portal credentials.
- **Network:** VPC; DB and workers in private subnets; HTTPS only at edge.

---

## 5. Observability (Target)

- **Logging:** Structured logs from FastAPI, bulk workers, OCR, and Playwright steps.
- **Metrics:** Job success/failure, queue depth, API latency, and automation completion rate.
- **Artifacts:** `ocr_output/<dealer>/<subfolder>/` stores OCR output plus `DMS_Form_Values.txt` and `Vahan_Form_Values.txt` for operator traceability. Real Siebel fill writes a new **`Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt`** execution log (IST wall clock; steps, values, decisions) into that subfolder on each run so retries keep a sequence of files.
- **Playwright:** The API does not call `Browser.close()` or `Playwright.stop()` for Fill DMS / CDP reuse / RTO payment flows (process exit and thread switches included); operator Edge/Chrome stays open. Orphaned Playwright drivers may accumulate on thread switches or repeated RTO runs.
- **Alerts:** DLQ growth, high error rate, DB health, and worker lease/retry anomalies.

---

## 6. Repository and Docs

- **Code:** Monorepo or split (e.g. `client/`, `backend/`, `workers/`).
- **Docs:** Kept under `Documentation/`:
  - `business-requirements-document.md` — BRD, business rules, functional requirements
  - `high-level-design.md` — HLD, code structure, data flows
  - `low-level-design.md` — LLD, API endpoints, modules
  - `Database DDL.md` — all tables, columns, constraints, usage
  - `technical-architecture.md` (this file)
  - `checkpoints.md` — **canonical registry** of named git checkpoints (tags, commits, IST dates, TODOs). Optional per-checkpoint narrative: `checkpoint-*.md`. Creating a checkpoint without updating this registry is disallowed—see §6 and **`.cursor/rules/checkpoints-registry.mdc`**.

### Documentation Maintenance

**Requirement:** Any substantive code, configuration, or database change must be reflected in **`Documentation/`** in the same delivery (same PR or same session), not left as a follow-up unless the product owner explicitly defers it.

Update as applicable:

- **BRD** (`business-requirements-document.md`) — business rules, §6.1a Siebel target sequence, functional requirements; add **changelog** rows for notable behavior changes
- **HLD** (`high-level-design.md`) — backend modules, client pages; add **changelog** row when the architecture or module contract changes
- **LLD** (`low-level-design.md`) — API tables, §2.4d Playwright parity, module notes; add **LLD changelog** (e.g. **6.x**) for non-trivial automation or API changes
- **Database DDL** (`Database DDL.md`) — only when **`DDL/`** scripts or **table/column/schema** definitions change; do **not** add changelog rows for automation-only or API-only work with no database change
- **This file** (§6–§7) — optional changelog row when documentation policy or repo doc layout changes

Quick mapping:

- **New API endpoint** → LLD (API Endpoints table), HLD (backend modules)
- **New DB table/column** → `DDL/` scripts, `Database DDL.md`
- **New business rule** → BRD (Business Rules section)
- **New page or flow** → BRD (FRs), HLD (client pages, data flow)
- **Queue/storage/runtime behavior changes** → technical architecture (as needed), HLD, LLD; **Database DDL** only if schema changes

Cursor rule: `.cursor/rules/documentation-maintenance.mdc` (always applied).

### Optional: Cursor Bugbot (automated PR review)

**Bugbot** is not installed as a classic editor extension from the VS Marketplace. Enable it from the **[Cursor dashboard](https://cursor.com/dashboard)** → **Integrations**: connect **GitHub** or **GitLab**, then turn on Bugbot for the repositories you want. It can comment on pull requests with findings; see the **[Cursor Bugbot documentation](https://cursor.com/docs/bugbot)** (and [Bugbot product page](https://cursor.com/bugbot) for overview).

---

## 7. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial technical architecture |
| 0.2 | Mar 2026 | — | Updated queue, worker, local file storage, bulk processing, and automation artifact architecture |
| 0.3 | Mar 2026 | — | §6 optional **Cursor Bugbot** setup (dashboard/Git integration, not VSIX) |
| 0.4 | Mar 2026 | — | §3.1 **External portal URLs** — **`backend/.env`** for DMS/Siebel, VAHAN, Insurance; **`DMS_MODE`** default **real** |
| 0.5 | Mar 2026 | — | §6 **Documentation Maintenance** — mandatory alignment of BRD / HLD / LLD / **Database DDL.md** with code and schema changes; **`Database DDL.md`** changelog may record “no schema change”; **`.cursor/rules/documentation-maintenance.mdc`** |
| 0.6 | Apr 2026 | — | §5 **Artifacts:** Siebel **`Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt`** per run (IST), not a single overwritten **`Playwright_DMS.txt`** — **LLD** **6.117** |
| 0.7 | Apr 2026 | — | §6 **`Database DDL.md`** updated **only** for real schema / **`DDL/`** changes (not automation-only); **`.cursor/rules/documentation-maintenance.mdc`** aligned |
| 0.8 | Apr 2026 | — | §6 **`checkpoints.md`** as canonical checkpoint registry; mandatory registration + **`.cursor/rules/checkpoints-registry.mdc`** |
