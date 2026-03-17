# High Level Design (HLD)
## Auto Dealer Management System

**Version:** 0.2  
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
| **Client (React)** | UI, forms, validation, calls to backend API; Add Sales, Fill Forms, RTO Payments, View Customer. |
| **FastAPI App** | REST API, CRUD, Submit Info, Fill DMS, Form 20, Vahan, RTO payment, customer search, OCR queue. |
| **PostgreSQL** | Persistent store for dealers, vehicles, customers, sales, insurance, RTO payments, service reminders. |
| **Queue (Redis or SQS)** | Decouple job creation from execution; OCR queue and Automation queue. |
| **OCR Worker** | Consumes OCR jobs; runs Tesseract (or Textract/Vision); writes results to DB. |
| **Playwright Worker** | Consumes automation jobs; reads DB, drives browser, submits to DMS and Vahan. |
| **Object Store (S3 or local)** | Uploaded scans, Form 20/21/22 PDFs. |

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
| `routers/submit_info` | Upsert customer, vehicle, sales, insurance. |
| `routers/rto_payment_details` | List RTO applications, record payment. |
| `routers/customer_search` | Search customers by mobile/plate. |
| `routers/dealers` | Get dealer by ID. |
| `routers/documents` | List/download documents by subfolder. |
| `routers/qr_decode` | Decode Aadhar QR. |
| `routers/vision` | Vision API (Aadhar analyze). |
| `routers/textract_router` | AWS Textract extraction. |
| `services/form20_service` | Form 20 generation (Word/PDF/HTML). |
| `services/fill_dms_service` | Playwright DMS and Vahan automation. |
| `services/submit_info_service` | Submit Info business logic. |
| `services/rto_payment_service` | RTO payment updates. |
| `repositories/*` | Data access for ai_reader_queue, dealer_ref, rto_payment_details, rc_status_sms_queue. |

### 3.3 Client Pages

| Page | Purpose |
|------|---------|
| `AddSalesPage` | Add Sales flow: Submit Info, Fill Forms (DMS, Vahan, Form 20), Insurance. |
| `RtoPaymentsPendingPage` | List pending RTO applications; record payment. |
| `ViewCustomerPage` | Search customer; view vehicles and insurance. |
| `AiReaderQueuePage` | OCR queue status and processing. |
| `PlaceholderPage` | Coming-soon placeholder. |

---

## 4. Data Flow (High Level)

### 4.1 Add Sales Flow

1. User uploads scans → `uploads/scans` → ai_reader_queue.
2. OCR processes queue → extracted text stored.
3. User reviews/corrects → Submit Info → customer_master, vehicle_master, sales_master, insurance_master.
4. Fill DMS → Playwright logs in, searches vehicle, scrapes data, downloads Form 21/22; vehicle_master updated.
5. Print Form 20 → form20_service fills Word template, converts to PDF, saves Form 20.pdf.
6. Vahan → Playwright fills RTO portal → rto_payment_details created (status Pending).
7. RTO Payments Pending → user records payment → status Paid.

### 4.2 Service Reminders Flow

1. sales_master upsert (when dealer has auto_sms_reminders = Y).
2. Trigger `fn_sales_master_sync_service_reminders` runs.
3. Inserts rows into service_reminders_queue from oem_service_schedule.

---

## 5. Deployment Topology (Target)

- **Client:** Installed or accessed from dealer workstations (browser or Electron).
- **AWS:** VPC with private subnets for app, workers, DB; public subnets for load balancer; RDS PostgreSQL; S3; SQS or ElastiCache Redis; ECS Fargate (or similar) for FastAPI and workers.

---

## 6. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Client lightweight | Logic and scaling on server; simple client install and updates. |
| Queue between API and workers | Reliability, retries, and independent scaling of workers. |
| PostgreSQL as system of record | Strong consistency, relational model for dealers/vehicles/sales. |
| Playwright for automation | Reliable browser control for filling DMS and Vahan. |
| Form 20: Word → PDF → HTML fallback | Prefer Word template; LibreOffice/docx2pdf for conversion; HTML when conversion unavailable. |
| sales_id as PK | Enables FK from rto_payment_details and service_reminders_queue; one sale per (customer, vehicle). |

---

## 7. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial HLD |
| 0.2 | Mar 2025 | — | Added code structure (3.1–3.3), backend modules, client pages, Add Sales flow, Service Reminders flow |
