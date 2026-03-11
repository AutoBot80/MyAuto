# Technical Architecture
## Auto Dealer Management System

**Version:** 0.1  
**Last Updated:** March 2025

---

## 1. Technology Stack

| Layer | Technology | Notes |
|-------|------------|--------|
| Client | React (TypeScript), Vite | Light UI; basic validation only. |
| API | Python 3.x, FastAPI | REST API, CORS, auth (planned). |
| Database | PostgreSQL | RDS on AWS; local for dev. |
| Queue | Redis or AWS SQS | Job queues for OCR and automation. |
| OCR | Tesseract (Python: pytesseract) | Run in containerized worker. |
| Browser automation | Playwright (Python) | Headless browser for portal submission. |
| Object storage | AWS S3 | Documents and artifacts. |
| Hosting (target) | AWS | VPC, ECS Fargate or similar, ALB, RDS, S3, SQS or ElastiCache. |

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
│  │ S3          │<---│ Redis / SQS  │    │  OCR Worker (ECS)    │  │
│  │ (documents) │    │              │--->│  - Tesseract         │  │
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
| Redis | `docker run --name my-redis -p 6379:6379 -d redis`. |
| (Future) OCR / Playwright workers | Run locally or in Docker; point to local DB and queue. |

---

## 4. Security (Target)

- **Authentication:** JWT or OAuth2; refresh tokens; no secrets in client code.
- **Authorization:** All APIs scoped by `dealer_id` (tenant); RBAC for admin.
- **Secrets:** AWS Secrets Manager or SSM for DB and portal credentials.
- **Network:** VPC; DB and Redis in private subnets; HTTPS only at edge.

---

## 5. Observability (Target)

- **Logging:** Structured logs (FastAPI, workers) to CloudWatch.
- **Metrics:** Job success/failure, queue depth, API latency.
- **Alerts:** DLQ growth, high error rate, DB/Redis health.

---

## 6. Repository and Docs

- **Code:** Monorepo or split (e.g. `client/`, `backend/`, `workers/`).
- **Docs:** Kept under `Documentation/`:
  - `business-requirements-document.md`
  - `high-level-design.md`
  - `low-level-design.md`
  - `technical-architecture.md` (this file).

---

## 7. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial technical architecture |
