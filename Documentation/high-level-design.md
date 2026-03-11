# High Level Design (HLD)
## Auto Dealer Management System

**Version:** 0.1  
**Last Updated:** March 2025

---

## 1. System Context

```
                    +------------------+
                    |  Dealer Client   |
                    |  (React / local) |
                    +--------+---------+
                             |
                             | HTTPS
                             v
                    +------------------+
                    |   API Gateway    |
                    |   or ALB (AWS)   |
                    +--------+---------+
                             |
                             v
    +----------------+-------+-------+----------------+
    |                |       |       |                |
    v                v       v       v                v
+--------+    +--------+ +--------+ +--------+   +--------+
| FastAPI|    |  OCR   | | Playwr.| | Redis  |   | Postgres|
|  App   |    | Worker | | Worker | | / SQS  |   |   DB    |
+--------+    +--------+ +--------+ +--------+   +--------+
    |                |       |       ^                ^
    |                |       |       |                |
    +----------------+-------+-------+----------------+
                             |
                    +--------+--------+
                    |   Object Store  |
                    |   (e.g. S3)    |
                    +-----------------+
```

---

## 2. Main Building Blocks

| Component | Responsibility |
|-----------|----------------|
| **Client (React)** | UI, forms, validation, calls to backend API; displays job status. |
| **FastAPI App** | REST API, auth, CRUD, job creation (OCR + automation), integration with DB and queue. |
| **PostgreSQL** | Persistent store for dealers, users, vehicles, customers, deals, documents, jobs. |
| **Queue (Redis or SQS)** | Decouple job creation from execution; OCR queue and Automation queue. |
| **OCR Worker** | Consumes OCR jobs; downloads file, runs Tesseract, writes results to DB/S3. |
| **Playwright Worker** | Consumes automation jobs; reads DB, drives browser, submits to external portals. |
| **Object Store (S3)** | Raw and processed documents; optional screenshots/artifacts from automation. |

---

## 3. Data Flow (High Level)

1. **User action in client** (e.g. upload document, "Send to portal").
2. **Client** sends request to **FastAPI**.
3. **FastAPI** validates, writes to **PostgreSQL**, enqueues job to **Redis/SQS**.
4. **Worker** (OCR or Playwright) picks up job, processes, updates DB and optionally S3.
5. **Client** sees updated status via polling or future WebSocket/SSE.

---

## 4. Deployment Topology (Target)

- **Client:** Installed or accessed from dealer workstations (browser or Electron).
- **AWS:** VPC with private subnets for app, workers, DB; public subnets for load balancer; RDS PostgreSQL; S3; SQS or ElastiCache Redis; ECS Fargate (or similar) for FastAPI and workers.

---

## 5. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Client lightweight | Logic and scaling on server; simple client install and updates. |
| Queue between API and workers | Reliability, retries, and independent scaling of workers. |
| PostgreSQL as system of record | Strong consistency, relational model for dealers/vehicles/deals. |
| Playwright for automation | Reliable browser control for filling external portals. |

---

## 6. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial HLD |
