# High Level Design (HLD)
## Auto Dealer Management System

**Version:** 1.15  
**Last Updated:** April 2026

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
| **PostgreSQL** | Persistent store for dealers, vehicles, customers, sales, insurance, RTO payments, service reminders, and **`add_sales_staging`** rows (OCR merge snapshot; **LLD §2.2a**). **Generate Insurance** uses **`form_insurance_view`** (post–Create Invoice) **and** **`add_sales_staging.payload_json`** via **`staging_id`** as the joint input set (**BR-20**). |
| **Queue (local or SQS)** | Decouple bulk job creation from execution; current bulk processing uses SQS or a local in-process fallback queue. |
| **OCR Worker** | Runs OCR/pre-OCR, writes extracted artifacts to `ocr_output`, and supports bulk processing. |
| **Playwright Worker** | DMS: fill row from **`form_dms.py`** / staging JSON (**BR-9**); Vahan/insurance: views and persisted records as specified. Reuses already open DMS/Vahan tabs when available, can auto-open Edge/Chrome when no detectable tab exists, and writes form trace artifacts. It must not infer, default, remember, or interpret field values outside approved sources. |
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
| `routers/fill_forms_router` | Fill Forms (Playwright): DMS, Vahan, Form 20, Hero Insurance; optional **`POST /fill-forms/dms/warm-browser`** after Add Sales upload to pre-open DMS. |
| `routers/bulk_loads` | Bulk hot-table dashboard APIs, retry prep, action-taken tracking, and folder browsing. |
| `routers/submit_info` | Upsert customer, vehicle, sales, insurance; draft **`add_sales_staging`** row + **`staging_id`** in response. |
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
| `services/handle_browser_opening` | CDP and managed-browser helpers (`get_or_open_site_page`, tab matching, optional auto-login wait) shared by Fill DMS, Vahan, and Insurance. |
| `services/fill_hero_dms_service` | Playwright DMS (Siebel only: `siebel_dms_playwright.Playwright_Hero_DMS_fill` / `run_hero_siebel_dms_flow` — **Find Contact Enquiry** path; **LLD §2.4d** / **6.105**). **`prepare_vehicle`** runs before Contact Find; `DMS_SIEBEL_*` / `DMS_REAL_URL_*`. DMS fill from **`form_dms.py`** or **`add_sales_staging.payload_json`**; **`collate_customer_master_from_dms_siebel_inputs`** builds **`out["dms_customer_master_collated"]`** after payments (video SOP, **LLD** **6.108**, **6.109**). Vaahan helpers stubbed until production automation; reuses open tabs via `handle_browser_opening.get_or_open_site_page`; writes traces under `ocr_output/`. |
| `services/fill_hero_insurance_service` | Hero MISP: **`pre_process`** delegates to **`run_fill_insurance_only`**; **`main_process`** / **`post_process`** complete Hero GI. Uses `handle_browser_opening.get_or_open_site_page`; `insurance_form_values` / `insurance_kyc_payloads` / `utility_functions`; writes `Insurance_Form_Values.txt` and `Playwright_insurance.txt`. Public API: **`POST /fill-forms/insurance/hero`** only (no separate bare insurance route). |
| `services/submit_info_service` | Submit Info business logic; builds staging **`payload_json`** and calls **`persist_staging_for_submit`**. |
| `services/rto_payment_service` | Dealer-scoped RTO batch runner, progress state, advisory locking, scrape-back persistence into `rto_queue` / `vehicle_master`, and downstream payment updates. |
| `repositories/*` | Data access for ai_reader_queue, bulk_loads, dealer_ref, **`form_dms`** (DMS fill row SQL, no view), `form_vahan_view`, rto_queue, rc_status_sms_queue. |

### 3.3 Client Pages

| Page | Purpose |
|------|---------|
| `AddSalesPage` | Add Sales flow: **Submit Info** persists **draft** **`add_sales_staging`** (`staging_id`); Hero OEM (**`oem_id`** from **`GET /dealers/:id`**) + financier starting with “Bajaj” → staging **`customer.financier`** = **Hinduja** + in-form note. **Create Invoice** (DMS) uses **`staging_id`** then commits masters and returns **IDs**; **Generate Insurance** uses **IDs** + **`staging_id`** with **`form_insurance_view`** (**BR-20**). Eligibility from `GET /add-sales/create-invoice-eligibility` (chassis, engine, mobile — **no** dealer filter). |
| `SubdealerChallanPage` | POS Saathi — Subdealer Challan: From/To, dealer/sub-dealer field, upload/scanner actions, Create Challans, and five side-by-side chassis entry tables (12 rows each, S.No. 1–60). |
| `BulkLoadsPage` | View hot bulk processing status, unresolved failures, and retry/corrective actions. |
| `RtoPaymentsPendingPage` | List queued RTO work items, start the oldest-7 dealer batch, and show live RTO Cart progress. |
| `ViewCustomerPage` | Search customer; view vehicles, insurance, and the bottom single-row `form_vahan_view` projection for the selected vehicle. |
| `HomePage` | Landing page with POS, RTO, Service, Dealer, and Admin tiles; Admin Saathi can clear non-reference-table data after confirmation. |
| `AiReaderQueuePage` | OCR queue status and processing. |
| `PlaceholderPage` | Coming-soon placeholder. |

---

## 4. Data Flow (High Level)

### 4.1 Add Sales Flow

1. User uploads scans → `uploads/scans` → ai_reader_queue (legacy) **or** Add Sales v2 → `uploads/scans-v2` → `OcrService.process_uploaded_subfolder` in the same request (optional AWS Textract prefetch; Aadhaar uses **Textract text only** — no UIDAI QR in this path; parallel Aadhaar assembly + Details sheet compile; merged JSON; optional `extraction.section_timings_ms` on the response; upload status line shows timings).
   - **Section 2 (AI extracted information):** Customer, Vehicle, and Insurance subsection headers show **Uploading…** while files are uploading and **Processing…** until extraction for that block is populated; the client polls `getExtractedDetails` until customer, vehicle, and insurance blocks all satisfy the same completion rules (or polling limits apply).
2. OCR processes queue → extracted text stored.
3. User reviews/corrects → Submit Info → customer_master, vehicle_master, sales_master, insurance_master.
4. Fill DMS → Playwright loads DMS field values from **OCR merge in staging** (target) or **`form_dms.py`** inline query over masters (legacy after Submit), reuses an already open DMS tab when detectable (CDP), or opens Edge/Chrome. **Hero Connect / Siebel** (default **`DMS_MODE=real`**): `run_hero_siebel_dms_flow` follows **BRD §6.1a**; **LLD §2.4d** lists heuristics and gaps. Static training DMS HTML was removed. Writes DMS traces; updates `vehicle_master` from scrape when data is returned.
5. Print Form 20 → `form20_service` fills the Word template, converts to PDF, and saves Form 20.pdf / Gate Pass.pdf in the upload subfolder.
6. RTO queue insertion → Fill Forms stores Form 20 outputs, estimates the RTO fees, and inserts an `rto_queue` row instead of auto-running the dummy Vahan flow.
7. RTO Queue → operators review queued rows in `RTO Saathi`, process the oldest 7 rows by reusing already open Vahan tabs (or auto-opened Edge/Chrome tabs when unavailable), and wait for live progress up to the upload/cart checkpoint.

### 4.2 Service Reminders Flow

1. `sales_master` INSERT or UPDATE (relevant when `dealer_ref.auto_sms_reminders = Y`).
2. Trigger `fn_sales_master_sync_service_reminders` runs on the database server.
3. Trigger deletes prior rows for that `sales_id` and may INSERT into `service_reminders_queue` from `oem_service_schedule` (service_num = 1 path per current function).
4. **No parallel app path:** the API and workers do not write `service_reminders_queue` directly — **BR-6** / **LLD §2.2a** lock trigger-only maintenance.

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

### 5.1 DMS Mapping (fill row — former `form_dms_view` projection)

The SQL view **`form_dms_view`** is **removed**; the same mapping is implemented in **`backend/app/repositories/form_dms.py`** (and will be satisfied from **`add_sales_staging.payload_json`** for the staging-first path).

| DMS label | Result key | DB source expression |
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
| Nominee Gender (details-sheet capture context) | `insurance_master.nominee_gender` (staging in `add_sales_staging.payload_json.insurance` until commit) |

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
| New Policy (`MispPolicy.aspx`) | Ex-Showroom | `vehicle_master.vehicle_ex_showroom_price` (view: `form_vahan_view.vehicle_price`) |
| New Policy (`MispPolicy.aspx`) | RTO | `dealer_ref.rto_name` |
| New Policy (`MispPolicy.aspx`) | Nominee Name / Age / Relation | `insurance_master.nominee_name`, `insurance_master.nominee_age`, `insurance_master.nominee_relationship` |
| New Policy (`MispPolicy.aspx`) | Nominee Gender | `insurance_master.nominee_gender` |
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

- Playwright runtime values for DMS must be sourced per **BR-9** (staging OCR JSON or **`form_dms.py`** master join); Vahan from **`form_vahan_view`** and persisted IDs.
- Playwright runtime values for Insurance must be sourced from **`form_insurance_view`** (sale-linked masters after Create Invoice) **together with** **`add_sales_staging.payload_json`** when Add Sales passes **`staging_id`** — the two sources are the **joint** approved input set (**BR-20**). **`OCR_To_be_Used.json`** is only an insurer fallback when view and staging lack insurer.
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
| 1.9 | Mar 2026 | — | **§4.1** step 4: **no contact match** branch — `_add_enquiry_opportunity` (vehicle + Opportunities row); see **LLD §2.4d** / **BRD §6.1a** step 2a |
| 1.10 | Mar 2026 | — | PostgreSQL **`add_sales_staging`** for Add Sales validate-then-commit-after-DMS (**LLD §2.2a**) |
| 1.11 | Mar 2026 | — | DMS fill without **`form_dms_view`** — **`form_dms.py`**, staging JSON; **`13b_drop_form_dms_view.sql`** |
| 1.12 | Mar 2026 | — | **BR-20**: Insurance fill — **`form_insurance_view`** + **`add_sales_staging.payload_json`** when **`staging_id`** passed |
| 1.13 | Mar 2026 | — | **§5.4** Insurance runtime rule aligned with **BR-20**; PostgreSQL building-block note for staging + GI |
| 1.14 | Mar 2026 | — | **BR-20**: view + **`payload_json`** as **joint** complete GI input set; **AddSalesPage** / **§5.4** — **`staging_id`** on Generate Insurance |
| 1.15 | Mar 2026 | — | **`fill_hero_dms_service`** / **`prepare_vehicle`**: Siebel vehicle prep sequence documented in **LLD §2.4d** / **6.72** and **BRD §6.1a** / **3.28** (mandatory left Search Results Title drill-in; dealer: Serial → Features → Pre-check/PDI) |
| 1.16 | Mar 2026 | — | Left-pane VIN drill-in: single-hit **Title** fallback when full VIN visible but only partial in DB — **LLD** **6.74**, **BRD** **3.29** |
| 1.17 | Mar 2026 | — | **`prepare_vehicle`**: HHML **Features** applet visibility detection — avoid redundant **VIN**/Serial grid clicks — **LLD** **6.75**, **BRD** **3.30** |
| 1.18 | Mar 2026 | — | **`prepare_vehicle`**: **Features in Vehicles** (**aria-label**) + **`s_vctrl_div`** third-level tab click for **Features** — **LLD** **6.76**, **BRD** **3.31** |
| 1.19 | Mar 2026 | — | **`prepare_vehicle`**: post–**Serial Number** drill, direct HHML **cubic**/**vehicle_type** scrape (no Features tab step) — **LLD** **6.77**, **BRD** **3.32** |
| 1.20 | Mar 2026 | — | **`prepare_vehicle`**: **`summary="Features"`** grid rows for **cubic**/**vehicle_type** — **LLD** **6.78**, **BRD** **3.33** |
| 1.21 | Mar 2026 | — | **`prepare_vehicle`**: explicit HHML id fallback scrape (including cell **`title`**) before Pre-check/PDI transition — **LLD** **6.79**, **BRD** **3.34** |
| 1.22 | Mar 2026 | — | **`prepare_vehicle`** / serial Pre-check-PDI: **`[frame-focus]`** diagnostic **`note`** lines — **LLD** **6.80**, **BRD** **3.35** |
| 1.23 | Mar 2026 | — | **`prepare_vehicle`** serial-detail rollback to prior working baseline (**commit `ab903064`**): Pre-check/PDI helper first (with feature-id scrape), then Features tab scrape — **LLD** **6.81**, **BRD** **3.36** |
| 1.24 | Mar 2026 | — | **`cubic_capacity`** normalized to numeric-only at scrape time — **LLD** **6.82**, **BRD** **3.37** |
| 1.25 | Mar 2026 | — | Payments flow reliability: primary short tab activation + **Ctrl+S** save fallback with Transaction# verification gate — **LLD** **6.83**, **BRD** **3.38** |
| 1.26 | Mar 2026 | — | Payments save order tuned: **Ctrl+S** primary, icon click fallback, Transaction# verification unchanged — **LLD** **6.84**, **BRD** **3.39** |
| 1.27 | Mar 2026 | — | **`fill_hero_dms_service`** / video SOP: temporary **`SIEBEL_DMS_HARD_FAIL_BEFORE_BOOKING_AND_ORDER`** stopped the run after payments — **LLD** **6.90** (**superseded by** **6.91**). |
| 1.28 | Mar 2026 | — | Logging cleanup: trial **`payment_lines_root_hint`** file append, UTC-duplicate diag **`note`** lines — **LLD** **6.91**, **BRD** **3.41** |
| 1.29 | Mar 2026 | — | **`SIEBEL_DMS_HARD_FAIL_BEFORE_BOOKING_AND_ORDER`** restored with owner-only removal policy (**`.cursor/rules/siebel-hard-fail-before-booking.mdc`**) — **LLD** **6.92** |
| 1.30 | Mar 2026 | — | **`fill_hero_dms_service`** / Add Enquiry: optional skip of second vehicle scrape when **`prepare_vehicle`** already merged — **LLD** **6.93**, **BRD** **3.42** (see **1.32** / **LLD** **6.95** for vehicle find always-on). |
| 1.31 | Mar 2026 | — | Add Enquiry: optional skip of entire **`_siebel_vehicle_find_chassis_engine_enter`** when merge ready — **LLD** **6.94**, **BRD** **3.43** (**superseded by** **1.32** / **LLD** **6.95** / **BRD** **3.44**). |
| 1.32 | Mar 2026 | — | Add Enquiry: always vehicle Find + VIN drill; merge-ready path skips duplicate scrape only — **LLD** **6.95**, **BRD** **3.44** |
| 1.33 | Mar 2026 | — | **`fill_hero_dms_service`** / Siebel: Contact Find strategy 1 (bounded waits) + strategy 2 (mobile-only Find then fallback) — **LLD** **6.96**, **BRD** **3.45** |
| 1.34 | Mar 2026 | — | Siebel **`[TRACE:FC→FN]`** execution log lines — **LLD** **6.97**, **BRD** **3.46** |
| 1.35 | Mar 2026 | — | Siebel Contact Find mobile drilldown iframe hint — **LLD** **6.98**, **BRD** **3.47** |
| 1.36 | Mar 2026 | — | Siebel **`Playwright_DMS.txt`** trial DOM hint lines (Title drilldown / Contact_Enquiry subgrid) — **LLD** **6.99**, **BRD** **3.48** |
| 1.37 | Mar 2026 | — | Siebel FC→FN contact readiness aligned with Contacts first-name drill probe — **LLD** **6.100**, **BRD** **3.49** |
| 1.38 | Mar 2026 | — | Siebel built-in Hero frame URL hints (Contact Find + Contact_Enquiry eval) — **LLD** **6.101**, **BRD** **3.50** |
| 1.39 | Mar 2026 | — | Siebel Contact Find drilldown fast path (hinted **Frame** + FrameLocators) — **LLD** **6.103**, **BRD** **3.51** |
| 1.40 | Mar 2026 | — | **`fill_hero_dms_service`**: removed Siebel FC→FN trace and trial **`note`** JSON diagnostics — **LLD** **6.104**, **BRD** **3.52** |
| 1.41 | Mar 2026 | — | **`fill_hero_dms_service`** / **`Playwright_Hero_DMS_fill`**: linear SOP removed; **Find Contact Enquiry** only — **LLD** **6.105**, **BRD** **3.53** |
| 1.42 | Mar 2026 | — | **`siebel_dms_playwright`**: deleted unused basic-enquiry / re-find / care-of-only helpers — **LLD** **6.106**, **BRD** **3.54** |
| 1.43 | Mar 2026 | — | **`_attach_vehicle_to_bkg`**: Pre-check/PDI after Allocate All disabled — **LLD** **6.107**, **BRD** **3.55** |
| 1.44 | Mar 2026 | — | **`fill_hero_dms_service`**: **`collate_customer_master_from_dms_siebel_inputs`** + **`Playwright_Hero_DMS_fill`** **`out["dms_customer_master_collated"]`** after payments — **LLD** **6.108**, **BRD** **3.56**, **Database DDL** **2.49** |
| 1.45 | Mar 2026 | — | Customer collate **`notes`** (detail sheet vs PK); **`dms_relation_prefix`** = address first three chars — **LLD** **6.109**, **BRD** **3.57**, **Database DDL** **2.50** |
| 1.46 | Mar 2026 | — | **`persist_dms_masters_atomic`**, attach ex-showroom scrape, **`dms_sales_master_prep`** + **`Playwright_DMS.txt`** master section — **LLD** **6.110**, **BRD** **3.58**, **Database DDL** **2.51** |
| 1.47 | Mar 2026 | — | **`insert_dms_masters_from_siebel_scrape`** (no prior customer/vehicle ids) vs UPDATE atomic when ids present — **LLD** **6.111**, **BRD** **3.59**, **Database DDL** **2.52** |
| 1.48 | Mar 2026 | — | Siebel master INSERT: **`file_location`** = **`ocr_output/{dealer_id}/{mobile}_{ddmmyyyy}`** (see **`resolve_ocr_sale_folder_paths`**) — **LLD** **6.112**, **BRD** **3.60**, **Database DDL** **2.53** |
| 1.49 | Mar 2026 | — | Minimal DB during DMS: staging-only on Submit Info; master INSERTs only after **Invoice#** scrape — **LLD** **6.113**, **BRD** **3.61**, **Database DDL** **2.54** |
| 1.50 | Mar 2026 | — | Add Sales client: Hero (**`oem_id`** = 1) + financier **Bajaj\*** prefix → staging **`financier`** **Hinduja** + UI note — **LLD** **6.114**, **BRD** **3.62**, **Database DDL** **2.55** |
| 1.51 | Apr 2026 | — | **`fill_hero_dms_service`** / **`Playwright_Hero_DMS_fill`**: **`_create_order`** My Orders pre-**+** grid branching; **`ready_for_client_create_invoice`** on **`out`** — **LLD** **6.115**, **BRD** **3.47**, **Database DDL** **2.56** |
| 1.52 | Apr 2026 | — | **`siebel_dms_playwright`**: no hard fail before **Generate Booking**; **`_attach_vehicle_to_bkg`** auto-clicks **Create Invoice**; IST timestamps — **LLD** **6.116**, **BRD** **3.48**, **Database DDL** **2.57** |
| 1.53 | Apr 2026 | — | **`fill_hero_dms_service`**: **`playwright_dms_execution_log_filename`** — per-run **`Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt`** (IST) vs overwritten **`Playwright_DMS.txt`** — **LLD** **6.117**, **BRD** **3.63**, **Database DDL** **2.58** |
| 1.54 | Apr 2026 | — | **`siebel_dms_playwright`**: **My Orders** grid classification + **unknown_rows** → **allocated** attach when Order# / no Invoice# — **LLD** **6.118**, **BRD** **3.64**, **Database DDL** **2.59** |
| 1.55 | Apr 2026 | — | **`_classify_my_orders_grid_rows`**: **allocated** before **pending** — **LLD** **6.119**, **BRD** **3.65**, **Database DDL** **2.60** |
| 1.56 | Apr 2026 | — | **`add_sales` router** + **AddSalesPage**: eligibility **`resolved_*`** ids — **LLD** **6.120**, **BRD** **3.66**, **Database DDL** **2.61** |
| 1.57 | Apr 2026 | — | **`fill_hero_insurance_service`**: MISP **Login** + **2W** clicks — **LLD** **6.121**, **BRD** **3.67** |
| 1.58 | Apr 2026 | — | **`fill_hero_insurance_service`**: **2W** **`aid="ctl00_TWO"`** — **LLD** **6.122**, **BRD** **3.68** |
| 1.59 | Apr 2026 | — | **`fill_hero_insurance_service`**: login **`type="submit"`** — **LLD** **6.123**, **BRD** **3.69** |
| 1.60 | Apr 2026 | — | **`fill_hero_dms_service`**: **`Playwright_DMS`** masters JSON after commit — **LLD** **6.124**, **BRD** **3.70** |
| 1.61 | Apr 2026 | — | **`fill_hero_insurance_service`**: **`[DIAG]`** page snapshots + iframe Sign In attempts — **LLD** **6.125**, **BRD** **3.71** |
| 1.62 | Apr 2026 | — | **`#root`** DIAG + scoped Sign In; **`Playwright_insurance_diag_fallback.txt`** — **LLD** **6.126**, **BRD** **3.72** |
| 1.63 | Apr 2026 | — | **`fill_hero_insurance_service`**: **`pre_process` → `main_process`** Playwright **Page** reuse — **LLD** **6.127**, **BRD** **3.73** |
| 1.64 | Apr 2026 | — | **`run_fill_insurance_only`**: Sign In + DIAG before KYC — **LLD** **6.128**, **BRD** **3.74** |
| 1.65 | Apr 2026 | — | **`_try_click_sign_in_inside_password_form`** (MISP **Get Price** vs **Sign In**) — **LLD** **6.129**, **BRD** **3.75** |
| 1.66 | Apr 2026 | — | **`_wait_for_partner_login_password_filled`** + filled-form **Sign In** — **LLD** **6.130**, **BRD** **3.76** |
| 1.67 | Apr 2026 | — | Sign In **4×** + URL navigation check — **LLD** **6.131**, **BRD** **3.77** |
| 1.68 | Apr 2026 | — | MISP partner **`requestSubmit`** + post-submit UI hint logging when login URL unchanged — **LLD** **6.132**, **BRD** **3.78** |
| 1.69 | Apr 2026 | — | Shared **`handle_browser_opening`**: single-tab reuse on independent Edge/Chrome launch; **`www.`** host match — **LLD** **6.133**, **BRD** **3.79** |
| 1.70 | Apr 2026 | — | Fill Insurance: **2W** + **New Policy** after Sign In; KYC wait without login bounce — **LLD** **6.134**, **BRD** **3.80** |
| 1.71 | Apr 2026 | — | MISP new-tab handoff after **2W** / **New Policy** — **LLD** **6.135**, **BRD** **3.81** |
| 1.72 | Apr 2026 | — | Insurance tab selection: ignore Siebel/DMS when both open — **LLD** **6.136**, **BRD** **3.82** |
| 1.73 | Apr 2026 | — | MISP **Policy Issuance** nav expand before **New Policy** — **LLD** **6.137**, **BRD** **3.83** |
| 1.74 | Apr 2026 | — | KYC **Insurance Company** typeahead: fuzzy match on dropdown options — **LLD** **6.138**, **BRD** **3.84** |
| 1.75 | Apr 2026 | — | Fill Insurance: CDP tab pick skips DMS; no Sign In on Siebel URL — **LLD** **6.139**, **BRD** **3.85** |
| 1.76 | Apr 2026 | — | **`Playwright_insurance.txt`** DIAG snapshots compact by default — **LLD** **6.140**, **BRD** **3.86** |
| 1.77 | Apr 2026 | — | KYC insurer field: **`ekycpage`** / hidden **`<select>`** + list matching — **LLD** **6.141**, **BRD** **3.87** |
| 1.78 | Apr 2026 | — | **`ekycpage`** KYC keyboard SOP (Tab/type/ArrowDown) — **LLD** **6.142**, **BRD** **3.88** |
| 1.79 | Apr 2026 | — | MISP KYC keyboard SOP URL gate + iframe focus before Tab chain — **LLD** **6.143**, **BRD** **3.89** |
| 1.80 | Apr 2026 | — | KYC OVD/mobile/consent DOM fallback in frame after insurer keyboard — **LLD** **6.144**, **BRD** **3.90** |
| 1.81 | Apr 2026 | — | KYC keyboard: focus insurer/mobile before type; guarded Select-All — **LLD** **6.145**, **BRD** **3.91** |
| 1.82 | Apr 2026 | — | KYC **`kyc_nav_scrape`** DIAG (visible controls + metrics) — **LLD** **6.146**, **BRD** **3.92** |
| 1.83 | Apr 2026 | — | KYC DIAG **`all_selects`** full **`<select>`** inventory — **LLD** **6.147**, **BRD** **3.93** |
| 1.84 | Apr 2026 | — | KYC **`kyc_nav_scrape_after_insurer`** DIAG — **LLD** **6.148**, **BRD** **3.94** |
| 1.85 | Apr 2026 | — | KYC insurer fuzzy + sequence fallback — **LLD** **6.149**, **BRD** **3.95** |
| 1.86 | Apr 2026 | — | KYC DIAG **networkidle** + partner scrape — **LLD** **6.150**, **BRD** **3.96** |
| 1.87 | Apr 2026 | — | **`dealer_ref.prefer_insurer`** + **`build_insurance_fill_values`** canonical insurer for KYC when merged details insurer fuzzy-matches (≥20%) — **LLD** **6.151**, **BRD** **3.97**, **Database DDL** **2.62** |
| 1.88 | Apr 2026 | — | **`fill_hero_insurance_service`** KYC: post-insurer **Tab** always; **KYC Partner** not changed (portal default) — **LLD** **6.152**, **BRD** **3.98** |
| 1.89 | Apr 2026 | — | **`fill_hero_insurance_service`** KYC: consent before primary CTA; already-verified banner branch; **Proceed** on **KYC Verification** button list — **LLD** **6.153**, **BRD** **3.99** |
| 1.90 | Apr 2026 | — | **`fill_hero_insurance_service`** KYC: banner copy match vs three-file upload path — **LLD** **6.154**, **BRD** **3.100** |
| 1.91 | Apr 2026 | — | **`fill_hero_insurance_service`** KYC: post-mobile branch (**Proceed** after mobile) — **LLD** **6.155**, **BRD** **3.101** |
| 1.92 | Apr 2026 | — | **`fill_hero_insurance_service`** MISP KYC keyboard: insurer **`Escape`**, DOM **OVD**, **`_kyc_restore_kyc_partner_to_default_label`** — **LLD** **6.156**, **BRD** **3.102** |
| 1.93 | Apr 2026 | — | **`fill_hero_insurance_service`** MISP KYC: **`_kyc_blur_if_insurer_product_select_focused`**, **`_kyc_fill_mobile_digits_in_frame`**, **`_kyc_frame_active_element_accepts_mobile_digits`** — **LLD** **6.157**, **BRD** **3.103** |
| 1.94 | Apr 2026 | — | **`fill_hero_insurance_service`** **`main_process`**: **`_hero_misp_fill_vin_txt_frame_no`**, **`_hero_misp_click_vin_page_submit`** — **LLD** **6.159**, **BRD** **3.104** |
| 1.95 | Apr 2026 | — | **`fill_hero_insurance_service`** VIN step: **`_hero_misp_wait_vin_shell_ready`**, framed **`txtFrameNo`**, **`_hero_misp_fill_vin_fallback_all_frames`** — **LLD** **6.160**, **BRD** **3.105** |
| 1.96 | Apr 2026 | — | **`fill_hero_insurance_service`**: KYC mobile wait vs **`divtxtFrameNo`** VIN — **LLD** **6.161**, **BRD** **3.106** |
| 1.97 | Apr 2026 | — | **Add Sales** (`AddSalesPage`): DOB input **`add-sales-v2-dl-input--dob`**; MISP **`_hero_misp_after_sign_in_settle`** — **LLD** **6.162** |
| 1.98 | Apr 2026 | — | **Insurance** **`.env`**: action/policy timeouts and DIAG/KYC flags — **LLD** **§2.5** |
| 1.99 | Apr 2026 | — | **`fill_hero_insurance_service`**: MISP **`INSURANCE_CLICK_SETTLE_MS`** + **`INSURANCE_*_IFRAME_SELECTOR`** as **module constants** (not **`.env`**); **`_hero_misp_wait_for_vin_txt_frame_no_attached`** after KYC **Proceed** (intermediate page) — **LLD** **6.165** |
| 1.100 | Apr 2026 | — | **`fill_hero_insurance_service`**: insurer **`Enter`**+blur+**`Tab`**; faster **Policy Issuance** expand; VIN frame ordering + default **`iframe[src*="2w" i]`** — **LLD** **6.166** |
| 1.101 | Apr 2026 | — | **Add Sales** (`AddSalesPage`): **Date of birth** — **`dd.add-sales-v2-dl-dd--dob`** fixes column width (overrides **`dd { flex: 1 }`** half-row stretch); input **`width: 100%`** inside capped **`dd`** — **LLD** **6.168** |
| 1.102 | Apr 2026 | — | **`fill_hero_insurance_service`**: **`vin_transition`** **DIAG** (KYC→VIN URL path + frame list; query length only) — **LLD** **6.167**, **BRD** **3.110** |
| 1.104 | Apr 2026 | — | **`main_process`**: **`_hero_misp_vin_step_timeout_ms`**, **`kyc_please_wait_overlay_visible`** — **LLD** **6.169**, **BRD** **3.111** |
| 1.105 | Apr 2026 | — | VIN: **`_hero_misp_wait_for_mispdms_vin_url_event`** (`wait_for_url`) + field **`wait_for`** — **LLD** **6.170** |
| 1.106 | Apr 2026 | — | **`fill_forms_router`** + **`/fill-forms`** API prefix; **`pre_process`** = **`run_fill_insurance_only`**; client **`fillForms.ts`** — **LLD** **6.171**, **BRD** **3.112** |
