# High Level Design (HLD)
## Auto Dealer Management System

**Version:** 0.4  
**Last Updated:** March 2026

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
| FastAPI|    |  OCR   | | Playwr.| | Local  |   | Postgres|
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
| **Client (React)** | UI, forms, validation, calls to backend API; Add Sales, Fill Forms, RTO Payments, View Customer, Bulk Loads, and Admin reset actions. |
| **FastAPI App** | REST API, CRUD, Submit Info, Fill DMS, Form 20, Vahan, RTO payment, customer search, OCR queue, bulk upload monitoring, and admin reset utilities. |
| **PostgreSQL** | Persistent store for dealers, vehicles, customers, sales, insurance, RTO payments, service reminders. |
| **Queue (local or SQS)** | Decouple bulk job creation from execution; current bulk processing uses SQS or a local in-process fallback queue. |
| **OCR Worker** | Runs OCR/pre-OCR, writes extracted artifacts to `ocr_output`, and supports bulk processing. |
| **Playwright Worker** | Reads DB-backed form views, reuses already open DMS/Vahan tabs when available, can auto-open Edge/Chrome when no detectable tab exists, and writes form trace artifacts. |
| **File Storage (local, optional S3 later)** | Uploaded scans, generated PDFs, and OCR/form-value artifacts. |

---

## 3. Code Development Structure

### 3.1 Repository Layout

```
My Auto.AI/
├── backend/                 # FastAPI backend
│   ├── app/
│   │   ├── main.py          # App factory, CORS, include_router
│   │   ├── config.py        # DATABASE_URL, UPLOADS_DIR, etc.
│   │   ├── db.py            # get_connection()
│   │   ├── routers/         # API route handlers
│   │   ├── services/        # Business logic
│   │   ├── repositories/    # Data access (DB only)
│   │   └── schemas/         # Pydantic request/response
│   ├── templates/           # HTML templates (e.g. Form 20)
│   └── requirements.txt
├── client/                  # React (Vite, TypeScript)
│   └── src/
│       ├── api/             # API client modules
│       ├── pages/           # Page components
│       ├── utils/           # Helpers, normalization
│       └── types/           # TypeScript types
├── DDL/                     # PostgreSQL DDL scripts
├── Documentation/           # BRD, HLD, LLD, Database DDL
└── Raw Scans/               # Form 20 Word template, etc.
```

### 3.2 Backend Modules

| Module | Purpose |
|--------|---------|
| `routers/health` | Liveness check. |
| `routers/uploads` | Document upload; enqueue to ai_reader_queue. |
| `routers/ai_reader_queue` | List, process, reprocess OCR queue items. |
| `routers/fill_dms` | Fill DMS (Playwright), Vahan, Form 20 print. |
| `routers/bulk_loads` | Bulk hot-table dashboard APIs, retry prep, action-taken tracking, and folder browsing. |
| `routers/submit_info` | Upsert customer, vehicle, sales, insurance. |
| `routers/rto_payment_details` | List and insert RTO queue rows, start dealer-scoped oldest-7 batch processing, expose batch progress, and optionally update payment later. |
| `routers/customer_search` | Search customers by mobile/plate and expose the read-only Vahan view row used by View Customer. |
| `routers/dealers` | Get dealer by ID. |
| `routers/documents` | List/download documents by subfolder. |
| `routers/admin` | Clear all non-reference-table data while preserving `oem_ref`, `dealer_ref`, and `oem_service_schedule`. |
| `routers/qr_decode` | Decode Aadhar QR. |
| `routers/vision` | Vision API (Aadhar analyze). |
| `routers/textract_router` | AWS Textract extraction. |
| `services/bulk_job_service` | Bulk ingest, queue publish, job lease, pre-OCR, and terminal status updates. |
| `services/bulk_queue_service` | Bulk queue abstraction with SQS or local in-process fallback. |
| `services/bulk_watcher_service` | Starts ingest and worker loops inside the API process. |
| `services/form20_service` | Form 20 generation (Word/PDF/HTML). |
| `services/fill_dms_service` | Playwright DMS and Vahan automation; reads fill values from `form_dms_view` / `form_vahan_view` only, reuses open logged-in site tabs when detectable, auto-opens Edge/Chrome when tabs are not found, writes `DMS_Form_Values.txt` and `Vahan_Form_Values.txt`, and returns operator guidance to login first-time and retry. |
| `services/submit_info_service` | Submit Info business logic. |
| `services/rto_payment_service` | Dealer-scoped RTO batch runner, progress state, advisory locking, scrape-back persistence into `rto_queue` / `vehicle_master`, and downstream payment updates. |
| `repositories/*` | Data access for ai_reader_queue, bulk_loads, dealer_ref, form_dms_view, form_vahan_view, rto_queue, rc_status_sms_queue. |

### 3.3 Client Pages

| Page | Purpose |
|------|---------|
| `AddSalesPage` | Add Sales flow: Submit Info, Fill Forms (DMS, Form 20, RTO queue insertion), Insurance. |
| `BulkLoadsPage` | View hot bulk processing status, unresolved failures, and retry/corrective actions. |
| `RtoPaymentsPendingPage` | List queued RTO work items, start the oldest-7 dealer batch, and show live RTO Cart progress. |
| `ViewCustomerPage` | Search customer; view vehicles, insurance, and the bottom single-row `form_vahan_view` projection for the selected vehicle. |
| `HomePage` | Landing page with POS, RTO, Service, Dealer, and Admin tiles; Admin Saathi can clear non-reference-table data after confirmation. |
| `AiReaderQueuePage` | OCR queue status and processing. |
| `PlaceholderPage` | Coming-soon placeholder. |

---

## 4. Data Flow (High Level)

### 4.1 Add Sales Flow

1. User uploads scans → `uploads/scans` → ai_reader_queue.
2. OCR processes queue → extracted text stored.
3. User reviews/corrects → Submit Info → customer_master, vehicle_master, sales_master, insurance_master.
4. Fill DMS → Playwright loads DMS field values from `form_dms_view`, reuses an already open DMS tab when detectable, or opens Edge/Chrome and asks operator login on first-time, then fills/scrapes vehicle data, stores DMS artifacts in `ocr_output`, and updates `vehicle_master`.
5. Print Form 20 → `form20_service` fills the Word template, converts to PDF, and saves Form 20.pdf / Gate Pass.pdf in the upload subfolder.
6. RTO queue insertion → Fill Forms stores Form 20 outputs, estimates the RTO fees, and inserts an `rto_queue` row instead of auto-running the dummy Vahan flow.
7. RTO Queue → operators review queued rows in `RTO Saathi`, process the oldest 7 rows by reusing already open Vahan tabs (or auto-opened Edge/Chrome tabs when unavailable), and wait for live progress up to the upload/cart checkpoint.

### 4.2 Service Reminders Flow

1. sales_master upsert (when dealer has auto_sms_reminders = Y).
2. Trigger `fn_sales_master_sync_service_reminders` runs.
3. Inserts rows into service_reminders_queue from oem_service_schedule.

### 4.3 Bulk Upload Flow

1. Operator drops a scan PDF into `Bulk Upload/<dealer_id>/Input Scans/`.
2. The API process can start bulk ingest and worker loops on startup, and the system also supports a standalone `run_bulk_worker.py` worker shape.
3. Ingest writes a hot `bulk_loads` row with `status='Queued'`, moves the file into the queued working area, and publishes a queue message through SQS or the local fallback queue.
4. A worker lease changes the hot row to `Processing`, then runs pre-OCR and Add Sales automation (including RTO queue insertion) and updates lifecycle fields (`job_status`, `processing_stage`, `attempt_count`, `leased_until`, `worker_id`).
5. OCR artifacts and form-value traces are written into `ocr_output/<dealer>/<subfolder>/` while upload/result folders hold customer-facing files.
6. Terminal rows stay in the hot `bulk_loads` table for the current UI; unresolved `Error` and `Rejected` rows remain visible until `action_taken=true`.

---

## 5. Deployment Topology (Target)

- **Client:** Installed or accessed from dealer workstations (browser or Electron).
- **AWS:** VPC with private subnets for app, workers, and DB; public subnets for load balancer; RDS PostgreSQL; SQS for bulk queueing; optional S3/object storage later; ECS Fargate (or similar) for FastAPI and workers.

---

## 6. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Client lightweight | Logic and scaling on server; simple client install and updates. |
| Queue between API and workers | Reliability, retries, and independent scaling of workers. |
| PostgreSQL as system of record | Strong consistency, relational model for dealers/vehicles/sales. |
| Playwright for automation | Reliable browser control for filling DMS and Vahan. |
| One browser session per dealer | Matches the RTO desk workflow while still allowing multiple dealers to process in parallel. |
| Form 20: Word → PDF → HTML fallback | Prefer Word template; LibreOffice/docx2pdf for conversion; HTML when conversion unavailable. |
| sales_id as PK | Enables FK from `rto_queue` and `service_reminders_queue`; one sale per (customer, vehicle). |

---

## 7. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial HLD |
| 0.2 | Mar 2025 | — | Added code structure (3.1–3.3), backend modules, client pages, Add Sales flow, Service Reminders flow |
| 0.3 | Mar 2026 | — | Added bulk upload router/services/pages, queue/deployment notes, and hot-table retention behavior |
| 0.4 | Mar 2026 | — | Updated for view-driven DMS/Vahan automation, `ocr_output` trace artifacts, and current queue/storage behavior |
| 0.5 | Mar 2026 | — | Added Admin Saathi home tile and backend reset capability for clearing non-reference-table data |
| 0.6 | Mar 2026 | — | Updated Playwright behavior to reuse already open DMS/Vahan tabs and return site-not-open errors when tabs are missing |
| 0.7 | Mar 2026 | — | Added fallback behavior to auto-open Edge/Chrome when tabs are not detectable and prompt operator first-time login + retry |
