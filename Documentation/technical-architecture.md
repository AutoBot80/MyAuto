# Technical Architecture
## Auto Dealer Management System

**Version:** 0.2  
**Last Updated:** March 2026

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
- **Artifacts:** `ocr_output/<dealer>/<subfolder>/` stores OCR output plus `DMS_Form_Values.txt` and `Vahan_Form_Values.txt` for operator traceability. Real Siebel fill writes a fresh `Playwright_DMS.txt` execution log (steps, values, decisions) into that subfolder each run.
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

### Documentation Maintenance

When adding or changing features, update the relevant docs:

- **New API endpoint** → LLD (API Endpoints table), HLD (backend modules)
- **New DB table/column** → `DDL/` scripts, `Database DDL.md`
- **New business rule** → BRD (Business Rules section)
- **New page or flow** → BRD (FRs), HLD (client pages, data flow)
- **Queue/storage/runtime behavior changes** → technical architecture, HLD, LLD, and Database DDL as applicable

### Optional: Cursor Bugbot (automated PR review)

**Bugbot** is not installed as a classic editor extension from the VS Marketplace. Enable it from the **[Cursor dashboard](https://cursor.com/dashboard)** → **Integrations**: connect **GitHub** or **GitLab**, then turn on Bugbot for the repositories you want. It can comment on pull requests with findings; see the **[Cursor Bugbot documentation](https://cursor.com/docs/bugbot)** (and [Bugbot product page](https://cursor.com/bugbot) for overview).

---

## 7. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial technical architecture |
| 0.2 | Mar 2026 | — | Updated queue, worker, local file storage, bulk processing, and automation artifact architecture |
| 0.3 | Mar 2026 | — | §6 optional **Cursor Bugbot** setup (dashboard/Git integration, not VSIX) |
