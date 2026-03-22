# High Level Design (HLD)
## Auto Dealer Management System

**Version:** 1.8  
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
| **Playwright Worker** | Reads DB-backed form views/records only for runtime values, reuses already open DMS/Vahan tabs when available, can auto-open Edge/Chrome when no detectable tab exists, and writes form trace artifacts. It must not infer, default, remember, or interpret field values outside persisted DB data. |
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
| `routers/settings` | Exposes automation site base URLs from env (`GET /settings/site-urls`) for the client; DMS/Vahan/Insurance URLs are required in `backend/.env` with no in-code fallbacks. |
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
| `services/fill_dms_service` | Playwright DMS, Vahan, and Insurance automation; reads fill values from DB-backed views/records only, reuses open logged-in site tabs when detectable, auto-opens Edge/Chrome when tabs are not found, writes `DMS_Form_Values.txt` / `Vahan_Form_Values.txt` / `Insurance_Form_Values.txt`, and returns operator guidance to login first-time and retry. DMS branch: `DMS_MODE=dummy` (static HTML) vs `DMS_MODE=real` (`siebel_dms_playwright.run_hero_siebel_dms_flow`, **BRD §6.1a**; `DMS_SIEBEL_*` / `DMS_REAL_URL_*` env). |
| `services/submit_info_service` | Submit Info business logic. |
| `services/rto_payment_service` | Dealer-scoped RTO batch runner, progress state, advisory locking, scrape-back persistence into `rto_queue` / `vehicle_master`, and downstream payment updates. |
| `repositories/*` | Data access for ai_reader_queue, bulk_loads, dealer_ref, form_dms_view, form_vahan_view, rto_queue, rc_status_sms_queue. |

### 3.3 Client Pages

| Page | Purpose |
|------|---------|
| `AddSalesPage` | Add Sales flow: Submit Info, then separate operator actions (`Fill DMS`, `Fill Insurance`, `Print Forms`); print step also inserts the RTO queue row. |
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
   - **Section 2 (AI extracted information):** Customer, Vehicle, and Insurance subsection headers show **Uploading…** while files are uploading and **Processing…** until extraction for that block is populated; the client polls `getExtractedDetails` until customer, vehicle, and insurance blocks all satisfy the same completion rules (or polling limits apply).
2. OCR processes queue → extracted text stored.
3. User reviews/corrects → Submit Info → customer_master, vehicle_master, sales_master, insurance_master.
4. Fill DMS → Playwright loads DMS field values from `form_dms_view`, reuses an already open DMS tab when detectable (CDP), or opens Edge/Chrome. **Hero Connect / Siebel** (`DMS_MODE=real`): `run_hero_siebel_dms_flow` follows **BRD §6.1a** (Find→mobile→Go, care-of vs full form, vehicle **In Transit** branch, booking/allotment, no Create Invoice); **LLD §2.4d** lists heuristics and gaps. **`DMS_MODE=dummy`:** static HTML linear path (no Create Invoice); order differs from §6.1a by design. Writes DMS traces; updates `vehicle_master` from scrape when data is returned.
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

## 5. Form Label to Database Mapping

This section defines database-to-label mapping contracts for DMS, Insurance, and Vahan forms.

### 5.1 DMS Mapping (`form_dms_view`)

| DMS label | View column | DB source expression |
|-----------|-------------|----------------------|
| Mr/Ms | `"Mr/Ms"` | Derived from `customer_master.gender` (`Ms.` when female, else `Mr.`) |
| Contact First Name | `"Contact First Name"` | First token from `customer_master.name` |
| Contact Last Name | `"Contact Last Name"` | Remaining tokens from `customer_master.name` |
| Mobile Phone # | `"Mobile Phone #"` | `customer_master.mobile_number::text` |
| Landline # | `"Landline #"` | `customer_master.alt_phone_num` |
| State | `"State"` | `UPPER(customer_master.state)` |
| Address Line 1 | `"Address Line 1"` | `customer_master.address` |
| Pin Code | `"Pin Code"` | `customer_master.pin` |
| Key num (partial) | `"Key num (partial)"` | `LEFT(COALESCE(vehicle_master.raw_key_num, vehicle_master.key_num, ''), 8)` |
| Frame / Chassis num (partial) | `"Frame / Chassis num (partial)"` | `LEFT(COALESCE(vehicle_master.raw_frame_num, vehicle_master.chassis, ''), 12)` |
| Engine num (partial) | `"Engine num (partial)"` | `LEFT(COALESCE(vehicle_master.raw_engine_num, vehicle_master.engine, ''), 12)` |

### 5.2 Insurance Mapping (Submit Info / `insurance_master`)

| Insurance form label (Add Sales) | Persisted column |
|----------------------------------|------------------|
| Insurer | `insurance_master.insurer` |
| Policy No | `insurance_master.policy_num` |
| Policy From | `insurance_master.policy_from` |
| Policy To | `insurance_master.policy_to` |
| Premium | `insurance_master.premium` |
| Nominee Name | `insurance_master.nominee_name` |
| Nominee Age | `insurance_master.nominee_age` |
| Nominee Relationship | `insurance_master.nominee_relationship` |
| Profession (details-sheet/insurance capture context) | `customer_master.profession` |
| Financier (details-sheet capture context) | `customer_master.financier` |
| Customer Marital Status (details-sheet capture context) | `customer_master.marital_status` |
| Nominee Gender (details-sheet capture context) | `customer_master.nominee_gender` |

### 5.2a Insurance Portal Mapping (Video-Aligned Labels)

| Portal page | Insurance label | DB source contract |
|-------------|------------------|--------------------|
| KYC (`ekycpage.aspx`) | Insurance Company | `insurance_master.insurer` (latest policy context for the sale/customer) |
| KYC (`ekycpage.aspx`) | Mobile No. | `customer_master.mobile_number` |
| MisDMS Entry (`MispDms.aspx`) | VIN Number | `COALESCE(vehicle_master.chassis, vehicle_master.raw_frame_num)` |
| New Policy (`MispPolicy.aspx`) | Proposer Name | `customer_master.name` |
| New Policy (`MispPolicy.aspx`) | Gender | `customer_master.gender` |
| New Policy (`MispPolicy.aspx`) | Alternate / Landline No. | `customer_master.alt_phone_num` |
| New Policy (`MispPolicy.aspx`) | Date of Birth | `customer_master.date_of_birth` (VARCHAR `dd/mm/yyyy`) |
| New Policy (`MispPolicy.aspx`) | Marital Status | `customer_master.marital_status` |
| New Policy (`MispPolicy.aspx`) | Occupation Type | `customer_master.profession` |
| New Policy (`MispPolicy.aspx`) | Proposer State / City / Pin / Address | `customer_master.state`, `customer_master.city`, `customer_master.pin`, `customer_master.address` |
| New Policy (`MispPolicy.aspx`) | Frame No. / Engine No. | `vehicle_master.chassis`, `vehicle_master.engine` |
| New Policy (`MispPolicy.aspx`) | Model Name / Fuel Type / Year of Manufacture | `vehicle_master.model`, `vehicle_master.fuel_type`, `vehicle_master.year_of_mfg` |
| New Policy (`MispPolicy.aspx`) | Ex-Showroom | `vehicle_master.vehicle_price` |
| New Policy (`MispPolicy.aspx`) | RTO | `dealer_ref.rto_name` |
| New Policy (`MispPolicy.aspx`) | Nominee Name / Age / Relation | `insurance_master.nominee_name`, `insurance_master.nominee_age`, `insurance_master.nominee_relationship` |
| New Policy (`MispPolicy.aspx`) | Nominee Gender | `customer_master.nominee_gender` |
| New Policy (`MispPolicy.aspx`) | Financer Name | `customer_master.financier` |

### 5.3 Vahan Mapping (`form_vahan_view`)

| Vahan label | View column | DB source |
|-------------|-------------|-----------|
| Registration Type * | `"Registration Type *"` | Constant in view (`New Registration`) |
| Chassis No * | `"Chassis No *"` | `COALESCE(rto_queue.chassis_num, vehicle_master.chassis, vehicle_master.raw_frame_num)` |
| Engine/Motor No (Last 5 Chars) | `"Engine/Motor No (Last 5 Chars)"` | `RIGHT(COALESCE(vehicle_master.engine, vehicle_master.raw_engine_num, ''), 5)` |
| Purchase Delivery Date | `"Purchase Delivery Date"` | `TO_CHAR(sales_master.billing_date, 'DD-MON-YYYY')` |
| Owner Name * | `"Owner Name *"` | `COALESCE(rto_queue.name, customer_master.name)` |
| Mobile No | `"Mobile No"` | `COALESCE(rto_queue.mobile, customer_master.mobile_number::text)` |
| Aadhaar No | `"Aadhaar No"` | Derived from `customer_master.aadhar` (last 4 marker text) |
| Permanent Address | `"Permanent Address"` | `customer_master.address` |
| Village/Town/City | `"Village/Town/City"` | `customer_master.city` |
| Insurance Type | `"Insurance Type"` | Derived from `insurance_master` latest row presence |
| Insurer | `"Insurer"` | latest `insurance_master.insurer` |
| Policy No | `"Policy No"` | latest `insurance_master.policy_num` |
| Insurance From (DD-MMM-YYYY) | `"Insurance From (DD-MMM-YYYY)"` | latest `insurance_master.policy_from` formatted |
| Insurance Upto (DD-MMM-YYYY) | `"Insurance Upto (DD-MMM-YYYY)"` | latest `insurance_master.policy_to` formatted |
| Insured Declared Value | `"Insured Declared Value"` | `COALESCE(insurance_master.idv::text, insurance_master.premium::text)` |
| Application No | `"Application No"` | latest `rto_queue.vahan_application_id` |
| Assigned Office & Action | `"Assigned Office & Action"` | Derived from latest dealer id (`RTO<dealer_id>`) |
| Registration No | `"Registration No"` | `vehicle_master.plate_num` |
| Amount | `"Amount"` | latest `rto_queue.rto_fees::text` |

### 5.4 Runtime Automation Rule

- Playwright runtime values for DMS/Vahan must be sourced from DB views/records (`form_dms_view`, `form_vahan_view`, and persisted IDs).
- Playwright runtime values for Insurance must be sourced from persisted DB records only (`customer_master`, `vehicle_master`, `insurance_master`, `dealer_ref` via sale linkage).
- Automation must not use fallback assumptions/default literals as data-entry substitutes when DB values are missing.
- Missing required DB values should fail fast with operator-visible validation, then retry after data correction.
- Insurance automation fills the policy form but must not press final Issue/Submit; the browser session remains open for operator control.

### 5.5 Video Reconciliation Step

- The operator video is the final UX truth for click-order and optional screen interactions.
- After video review, update Playwright step order documentation to match the confirmed sequence without changing the DB-mapping contract above.
- Insurance video-aligned reference flow is: login redirect -> KYC -> KYC success redirect -> MisDMS VIN entry -> New Policy creation (with optional Hero Connect lookup tab).

---

## 6. Deployment Topology (Target)

- **Client:** Installed or accessed from dealer workstations (browser or Electron).
- **AWS:** VPC with private subnets for app, workers, and DB; public subnets for load balancer; RDS PostgreSQL; SQS for bulk queueing; optional S3/object storage later; ECS Fargate (or similar) for FastAPI and workers.

---

## 7. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Client lightweight | Logic and scaling on server; simple client install and updates. |
| Queue between API and workers | Reliability, retries, and independent scaling of workers. |
| PostgreSQL as system of record | Strong consistency, relational model for dealers/vehicles/sales. |
| Playwright for automation | Reliable browser control for filling DMS and Vahan. |
| DB-first field population | Ensures deterministic, auditable automation and prevents accidental field assumptions. |
| One browser session per dealer | Matches the RTO desk workflow while still allowing multiple dealers to process in parallel. |
| Form 20: Word → PDF → HTML fallback | Prefer Word template; LibreOffice/docx2pdf for conversion; HTML when conversion unavailable. |
| sales_id as PK | Enables FK from `rto_queue` and `service_reminders_queue`; one sale per (customer, vehicle). |

---

## 8. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial HLD |
| 0.2 | Mar 2025 | — | Added code structure (3.1–3.3), backend modules, client pages, Add Sales flow, Service Reminders flow |
| 0.3 | Mar 2026 | — | Added bulk upload router/services/pages, queue/deployment notes, and hot-table retention behavior |
| 0.4 | Mar 2026 | — | Updated for view-driven DMS/Vahan automation, `ocr_output` trace artifacts, and current queue/storage behavior |
| 0.5 | Mar 2026 | — | Added Admin Saathi home tile and backend reset capability for clearing non-reference-table data |
| 0.6 | Mar 2026 | — | Updated Playwright behavior to reuse already open DMS/Vahan tabs and return site-not-open errors when tabs are missing |
| 0.7 | Mar 2026 | — | Added fallback behavior to auto-open Edge/Chrome when tabs are not detectable and prompt operator first-time login + retry |
| 0.8 | Mar 2026 | — | Added DMS/Insurance/Vahan label-to-DB mapping contract and strict DB-only Playwright runtime value rule |
| 0.9 | Mar 2026 | — | Updated Add Sales mapping: finance capture moved to Section 2 and persisted to `customer_master.financier`; added customer `marital_status` and `nominee_gender` capture fields |
| 1.0 | Mar 2026 | — | Added insurance portal (video-aligned) page-label-to-DB mapping and flow sequence for dummy insurance site + future Playwright parity |
| 1.1 | Mar 2026 | — | Added Insurance Playwright behavior contract: login fallback/open-browser, DB-only field fill, no final submit click, and session kept open |
| 1.2 | Mar 2026 | — | Updated Add Sales interaction model to split DMS/Insurance/Print actions into separate operator controls |
| 1.3 | Mar 2026 | — | Added Alternate/Landline mapping (`customer_master.alt_phone_num`) into DMS and Insurance label mapping contracts |
| 1.4 | Mar 2026 | — | Fill DMS flow: extended enquiry/stock/PDI/allocate/invoicing-line sequence; ex-showroom stored as `vehicle_price`; no auto Create Invoice |
| 1.5 | Mar 2026 | — | Add Sales Section 2: per-subsection upload/processing indicators; extraction polling continues until insurance block completes (aligned with UI rules) |
| 1.6 | Mar 2026 | — | DMS ``DMS_MODE`` (dummy vs real Siebel ``DMS_REAL_URL_*``); settings API exposes mode; Fill DMS real path navigates URLs only until Siebel selectors are added |
| 1.7 | Mar 2026 | — | Add Sales **§4.1** step 4: pointers to **BRD §6.1a** (target Siebel DMS sequence) and **LLD §2.4d** (Playwright parity table) |
| 1.8 | Mar 2026 | — | **§4.1** step 4: real Siebel implements §6.1a in code; dummy remains linear |
