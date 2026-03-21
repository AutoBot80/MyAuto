# Low Level Design (LLD)
## Auto Dealer Management System

**Version:** 1.0  
**Last Updated:** March 2026

---

## 1. Client (React) Structure (Modular)

- **Layout:** `AppLayout` composes `Header` + `Sidebar` + main content slot.
- **Pages:** `AddSalesPage`, `AiReaderQueuePage`, `BulkLoadsPage`, `RtoPaymentsPendingPage`, `ViewCustomerPage`, `HomePage`, `PlaceholderPage`.
- **API:** `api/client.ts` (base URL, `apiFetch`), `api/siteUrls.ts` (DMS/Vahan/Insurance bases from server `.env`), `api/uploads.ts`, `api/aiReaderQueue.ts`, `api/bulkLoads.ts`, `api/fillDms.ts`, `api/submitInfo.ts`, `api/rtoPaymentDetails.ts`, `api/customerSearch.ts`, `api/admin.ts` — microservice-friendly; swap base URL per env.
- **Hooks:** `useToday`, `useUploadScans`, `useAiReaderQueue` — reusable, testable.
- **Types:** `types/index.ts` — `Page`, `AddSalesStep`, `AiReaderQueueItem`, `ExtractedVehicleDetails`, `PrintForm20Response`, etc.

### 1.1 Key Components

| Component | Purpose |
|-----------|---------|
| `App` | Root; composes `AppLayout` and page by route. |
| `AppLayout` | Header + Sidebar + main slot. |
| `Header` | Dealer name (centre), right slot (e.g. date). |
| `Sidebar` | Nav links; `onNavigate(page)`. |
| `AddSalesPage` | Add Sales flow: Submit Info, Fill Forms & Print File (DMS, Form 20, Gate Pass, RTO queue insertion), Insurance tiles. |
| `UploadScansPanel` | Step tiles, file input, upload button, uploaded list. |
| `AiReaderQueuePage` | Uses `useAiReaderQueue`; renders `AiReaderQueueTable`. |
| `BulkLoadsPage` | Uses `api/bulkLoads`; shows hot processing rows, failure tabs, retry prep, and action-taken toggles. |
| `RtoPaymentsPendingPage` | List RTO applications; record payment. |
| `ViewCustomerPage` | Search by mobile/plate; view vehicles, insurance, and the selected vehicle's `form_vahan_view` row. |
| `HomePage` | Shows the main Saathi tiles and hosts the Admin Saathi reset button on the landing screen. |
| `PlaceholderPage` | Title + message for coming-soon pages. |

---

## 2. Backend (FastAPI) Structure (Modular / Microservice-Friendly)

### 2.1 Module Layout

```
backend/app/
  main.py              # App factory, CORS, include_router; validates DMS/VAHAN/INSURANCE base URLs at startup
  config.py            # DATABASE_URL, UPLOADS_DIR, APP_ROOT, FORM20_*, DMS_BASE_URL, VAHAN_BASE_URL, INSURANCE_BASE_URL (required), etc.
  db.py                # get_connection()
  schemas/             # Pydantic request/response (uploads, ocr, fill_dms, etc.)
  repositories/        # Data access only (ai_reader_queue, bulk_loads, dealer_ref, form_dms, form_vahan, rto_queue, rc_status_sms_queue)
  services/            # Business logic (UploadService, bulk_job_service, bulk_queue_service, bulk_watcher_service, form20_service, fill_dms_service, submit_info_service, rto_payment_service)
  routers/             # health, settings, uploads, ai_reader_queue, bulk_loads, fill_dms, submit_info, rto_queue, customer_search, dealers, documents, qr_decode, vision, textract_router
  templates/           # form20_front.html, form20_back.html, form20_page3.html
```

- **Routers:** Thin; call services or repos. Each router can be mounted or split into a separate service later.
- **Services:** Stateless, injectable (e.g. `UploadService(uploads_dir=...)`).
- **Repositories:** Table access only; no business rules. `form_dms.py` and `form_vahan.py` read the label-aligned DMS/Vahan views used by operator inspection and runtime export files.

### 2.2 API Endpoints (Current)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness. |
| GET | `/settings/site-urls` | Returns `dms_base_url`, `vahan_base_url`, `insurance_base_url` from `backend/.env` (required at server startup; used by the client for Fill DMS and messaging; no in-code URL fallbacks). |
| POST | `/uploads/scans` | Upload scans; enqueue to ai_reader_queue. |
| POST | `/uploads/scans-v2` | Upload scans (v2). |
| GET | `/ai-reader-queue` | List queue items (limit=200). |
| POST | `/ai-reader-queue/process-next` | Process oldest queued item with Tesseract. |
| GET | `/ai-reader-queue/extractions` | List queue items with extracted text. |
| GET | `/ai-reader-queue/extracted-details` | Get extracted details for subfolder. |
| GET | `/ai-reader-queue/insurance-extraction` | Insurance extraction. |
| GET | `/ai-reader-queue/process-status` | Process status. |
| POST | `/ai-reader-queue/empty` | Empty queue. |
| POST | `/ai-reader-queue/process-all` | Process all. |
| POST | `/ai-reader-queue/{item_id}/reprocess` | Reprocess item. |
| POST | `/submit-info` | Upsert customer, vehicle, sales, insurance. |
| GET | `/bulk-loads` | List bulk dashboard rows from hot `bulk_loads` only. |
| GET | `/bulk-loads/counts` | Bulk tab counts from hot data only; `Error` and `Rejected` exclude `action_taken=true`. |
| GET | `/bulk-loads/pending-count` | Count unresolved `Error` + `Rejected` hot rows for the nav badge. |
| PATCH | `/bulk-loads/{bulk_load_id}/action-taken` | Mark an `Error` or `Rejected` row corrected. |
| POST | `/bulk-loads/{bulk_load_id}/prepare-reprocess` | Copy error artifacts back to uploads and start OCR for manual retry. |
| PATCH | `/bulk-loads/{bulk_load_id}/mark-success` | Mark a manually completed error as success. |
| GET | `/bulk-loads/folder/{folder_path}/list` | List files in a bulk result folder. |
| GET | `/bulk-loads/file/{file_path}` | Download or preview a file from a bulk result folder. |
| POST | `/fill-dms` | Full Fill DMS flow; reuses already open logged-in DMS/Vahan tabs when detectable, otherwise auto-opens Edge/Chrome and returns first-time-login guidance. |
| POST | `/fill-dms/dms` | DMS only; reuses an already open logged-in DMS tab when detectable, otherwise auto-opens Edge/Chrome and asks operator to login first-time then retry. |
| GET | `/fill-dms/data-from-dms` | Get data from DMS.txt. |
| GET | `/fill-dms/form20-status` | Form 20 template status. |
| POST | `/fill-dms/print-form20` | Generate Form 20.pdf. |
| POST | `/fill-dms/vahan` | Vahan (RTO) only; reuses an already open logged-in Vahan tab when detectable, otherwise auto-opens Edge/Chrome and asks operator to login first-time then retry. |
| POST | `/fill-dms/insurance` | Insurance only; reuses an already open logged-in Insurance tab when detectable, otherwise auto-opens Edge/Chrome and asks operator to login first-time then retry. Fills fields only, does not click final submit/issue, and keeps browser open. |
| GET | `/rto-queue` | List RTO queue rows. |
| GET | `/rto-queue/by-sale` | Get RTO queue row by sale (customer_id, vehicle_id). |
| POST | `/rto-queue` | Create or update the queued RTO row for a sale. |
| POST | `/rto-queue/process-batch` | Start dealer-scoped processing of the oldest 7 queued rows through the upload/cart step. |
| GET | `/rto-queue/process-batch/status` | Get the live progress snapshot for the current dealer batch. |
| POST | `/rto-queue/{application_id}/retry` | Set one `Failed` queue row back to `Queued` so operators can retry from the UI. |
| POST | `/rto-queue/{application_id}/pay` | Optional downstream payment update. |
| GET | `/customer-search/search` | Search by mobile or plate. |
| GET | `/customer-search/form-vahan` | Get the `form_vahan_view` row for one customer/vehicle pair. |
| GET | `/dealers/{dealer_id}` | Get dealer by ID. |
| POST | `/admin/reset-all-data` | Truncate all public base tables except `oem_ref`, `dealer_ref`, and `oem_service_schedule`. |
| GET | `/documents/{subfolder}/list` | List documents in subfolder. |
| GET | `/documents/{subfolder}/{filename}` | Download document. |
| POST | `/qr-decode` | Decode Aadhar QR. |
| POST | `/vision/aadhar-analyze` | Vision API Aadhar analyze. |
| POST | `/textract/extract` | Textract extract. |
| POST | `/textract/extract-forms` | Textract extract forms. |
| GET | `/textract/extract-from-queue` | Extract from queue. |

### 2.3 Tesseract OCR Reader

- **Service:** `OcrService` in `services/ocr_service.py` — processes one queue item at a time (oldest first), runs Tesseract on the scan file under `UPLOADS_DIR/<subfolder>/<filename>`, writes extracted text to `OCR_OUTPUT_DIR` as `<subfolder>_<filename>.txt`.
- **Config:** `config.OCR_OUTPUT_DIR` (default: `backend/ocr_output`). Tesseract binary must be on system PATH (or set `pytesseract.pytesseract.tesseract_cmd`). `config.OCR_LANG` (default: `eng+hin`) for English + Hindi; see **Documentation/tesseract-ocr-setup.md** for installing Hindi tessdata for Aadhar scans.
- **Queue status:** `queued` → `processing` → `done` or `failed`.
- **Sales detail template mapping:** Details-sheet extraction also supports the A5 Sales Detail Sheet label style (e.g., `Full Name`, `Mobile Number`, `Aadhaar Number`, `Profession`, `Marital Status`, `Nominee ...`, `Financier Name`) and merges these into `OCR_To_be_Used.json` (`customer` + `insurance`) for Add Sales auto-population.

### 2.4 Form 20 Generation

- **Service:** `form20_service.py` — `generate_form20_pdfs(subfolder, customer, vehicle, vehicle_id, dealer_id)`.
- **Flow:** Prefer Word template (`templates/word/FORM 20 Template.docx`) → fill placeholders → convert to PDF (docx2pdf or LibreOffice) → output `Form 20.pdf` (all pages). Fallback: PDF overlay on Official FORM-20 or separate templates. Fallback: HTML templates. Override via env `FORM20_TEMPLATE_DOCX`.
- **Gate Pass:** Word template (`templates/word/Gate Pass Template.docx`) → fill placeholders → convert to PDF → output `Gate Pass.pdf`. Placeholders: `{{field_0_today_date}}`, `{{field_1_oem_name}}`, `{{field_2_customer_name}}`, `{{field_3_aadhar_id}}`, `{{field_4_model}}`, `{{field_5_color}}`, `{{field_6_key_num}}`, `{{field_7_chassis_num}}`. Override via env `GATE_PASS_TEMPLATE_DOCX`.
- **Placeholders:** `{{field_0_city}}`, `{{field_1_name}}`, `{{field_2_care_of}}`, `{{field_3_address}}`, `{{field_10_dealer_name}}`, `{{field_14_body_type}}`, `{{field_16_oem_name}}`, `{{field_17_year_of_mfg}}`, `{{field_20_cubic_capacity}}`, `{{field_21_model}}`, `{{field_22_chassis_no}}`, etc.

### 2.4a Dummy Vahan Flow

- **Static site:** `dummy-sites/vaahan/` simulates the real VAHAN navigation used by Playwright tests.
- **Pages:** landing/start (`index.html`) → owner/details entry (`application.html`) → assigned office/worklist (`search.html`) → payment gateway (`payment.html`) → bank login (`bank-login.html`) → bank confirmation (`bank-confirm.html`).
- **Automation contract:** `fill_dms_service.py` reads DMS field values only from `form_dms_view`, writes `ocr_output/<dealer>/<subfolder>/DMS_Form_Values.txt`, and updates `vehicle_master` with the DMS scrape (including `vehicle_price`). DMS/Vahan automation first attempts to reuse already open logged-in tabs; if no matching detectable tab is available, backend auto-opens Edge/Chrome to the target site and returns an operator message to login first-time and retry. The Add Sales page stops on that message and avoids downstream processing, then resumes normally on retry.

### 2.4b Dummy DMS Flow

- **Static site:** `dummy-sites/dms/` simulates the OEM DMS journey used by Playwright tests.
- **Pages:** Hero Connect login (`index.html`) → My Vehicle Sales (`my-sales.html`) → Line Items (`line-items.html`) → Auto Vehicle List (`vehicle.html`) → Auto Vehicle PDI Assessment (`pdi.html`) → MISP / Partner Login + reports/downloads (`reports.html`).
- **Automation contract:** `fill_dms_service.py` requires `customer_id` and `vehicle_id`, loads DMS field values from `form_dms_view`, fills only those view-backed values into a detectable logged-in DMS tab (or prompts first-time login after auto-opening browser when tab is unavailable), scrapes the first vehicle result row, persists the scrape into `vehicle_master`, and writes `Data from DMS.txt` plus `DMS_Form_Values.txt` into the matching `ocr_output` subfolder.

### 2.4c Dummy Insurance Flow

- **Static site:** `dummy-sites/insurance/` simulates the insurance issuance journey from the operator video.
- **Pages:** login redirection (`index.html`) -> KYC verification (`kyc.html`) -> KYC success redirect (`kyc-success.html`) -> MisDMS VIN entry (`dms-entry.html`) -> New Policy (`policy.html`) -> issue-result (`issued.html`).
- **Serve path:** `main.py` mounts this directory at `/dummy-insurance`.
- **Video-label parity:** top-level labels mirror observed strings (`Hero INSURANCE BROKING`, `HIBIPL - MisDMS Entry`, `New Policy - Two Wheeler`), including key menu items and KYC controls.
- **Current scope:** UI and navigation parity only (no backend insurance Playwright automation endpoint yet).
- **Automation contract:** Insurance Playwright uses persisted DB values only (`customer_master`, `vehicle_master`, `insurance_master`, `dealer_ref` mapping), fills policy fields, writes `Insurance_Form_Values.txt`, does not click final submit, and keeps the operator browser session open.

### 2.5 Database Access

- **Connection:** `get_connection()` in `db.py` using `DATABASE_URL`.
- **Usage:** Context manager (`with get_connection() as conn`) in route handlers; cursor for execute/fetch.
- **Transactions:** Commit on success; explicit or context-based.

---

## 3. Database Schema (Current)

See **Documentation/Database DDL.md** for full table structures. Summary:

| Table | Purpose |
|-------|---------|
| `ai_reader_queue` | OCR queue for uploaded scans. |
| `customer_master` | Customer data; unique (aadhar last 4, phone). |
| `vehicle_master` | Vehicle data; model, colour, oem_name, Form 20 fields. |
| `sales_master` | Links customer, vehicle, dealer; sales_id PK. |
| `oem_ref` | OEM/brand reference. |
| `oem_service_schedule` | Service schedule per OEM. |
| `dealer_ref` | Dealer reference; oem_id FK. |
| `insurance_master` | Insurance policies; FK to sales or (customer, vehicle). |
| `service_reminders_queue` | Service reminders; sales_id FK; populated by trigger. |
| `rto_queue` | RTO queue/worklist rows; one row per sale; application_id stays the stable queue id while `vahan_application_id` stores the real Vahan number once a dealer batch reaches the cart/upload checkpoint. |
| `form_dms_view` | Read-only DMS field projection that aligns DB-backed values to current DMS labels. |
| `form_vahan_view` | Read-only Vahan field projection that aligns DB-backed values to current Vahan labels. |
| `rc_status_sms_queue` | RC status SMS queue; sales_id FK. |
| `bulk_loads` | Hot operational bulk jobs, queue lifecycle, retry state, and operator actions. |

---

## 4. Queue and Workers (Planned)

### 4.1 Queues

- **ocr_queue:** Message = { document_id, s3_key, options }.
- **automation_queue:** Message = { job_id, target_system, entity_refs }.

### 4.2 OCR Worker

- Poll or subscribe to ocr_queue; download file from S3; run Tesseract; parse and store result in DB; update `ai_reader_queue` status.

### 4.3 Playwright Worker

- Poll or subscribe to automation_queue; load job; fetch related rows from DB; start browser; run site-specific script (login, navigate, fill, submit); update status and store artifacts.

### 4.4 Bulk Worker

- Ingest scans from `Bulk Upload/<dealer_id>/Input Scans/`, create hot `bulk_loads` rows with `status='Queued'`, move files into `Queued/`, and publish queue messages.
- Worker leases jobs through `bulk_loads` lease columns, switches the row to `Processing`, and processes them through pre-OCR, Add Sales, DMS, Form 20, RTO queue insertion, and terminal folder placement.
- API/UI reads remain hot-only for now. Older bulk rows are retained directly in `bulk_loads`.
- `Error` and `Rejected` rows remain visible in `bulk_loads` until `action_taken=true`.

---

## 5. Configuration and Environment

- **Backend:** `.env` in `backend/` with `DATABASE_URL`, `UPLOADS_DIR`, `OCR_OUTPUT_DIR`, `OCR_LANG`, `FORM20_TEMPLATE_*`, etc.
- **Client:** Base API URL (env or config) for `fetch` calls.

---

## 6. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial LLD |
| 0.2 | Mar 2025 | — | Updated backend modules, full API endpoints list, Form 20 section, database schema summary |
| 0.3 | Mar 2026 | — | Added bulk loads page/API details, backend bulk modules, and hot-table bulk worker behavior |
| 0.4 | Mar 2026 | — | Updated for `form_dms_view` / `form_vahan_view`, `ocr_output` automation traces, View Customer Vahan row, and current DMS/Vahan behavior |
| 0.5 | Mar 2026 | — | Added Admin Saathi landing-tile reset action and `/admin/reset-all-data` endpoint |
| 0.6 | Mar 2026 | — | Updated automation behavior to reuse already open logged-in DMS/Vahan tabs and return site-not-open errors when tabs are missing |
| 0.7 | Mar 2026 | — | Added fallback automation behavior to auto-open Edge/Chrome when tabs are not detectable and prompt first-time operator login + retry |
| 0.8 | Mar 2026 | — | Added dummy insurance site architecture/flow (`/dummy-insurance`) aligned to operator video navigation and labels |
| 0.9 | Mar 2026 | — | Added `/fill-dms/insurance` endpoint and Insurance Playwright contract (DB-only fill, no final submit click, keep browser open, operator-login fallback) |
| 1.0 | Mar 2026 | — | Updated OCR details-sheet mapping for A5 Sales Detail Sheet labels and merge behavior into AI-extracted customer/insurance fields |
