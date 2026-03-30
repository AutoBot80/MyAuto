# Low Level Design (LLD)
## Auto Dealer Management System

**Version:** 1.5  
**Last Updated:** March 2026

---

## 1. Client (React) Structure (Modular)

- **Layout:** `AppLayout` composes `Header` + `Sidebar` + main content slot.
- **Pages:** `AddSalesPage`, `AiReaderQueuePage`, `BulkLoadsPage`, `RtoPaymentsPendingPage`, `ViewCustomerPage`, `HomePage`, `PlaceholderPage`.
- **API:** `api/client.ts` (base URL, `apiFetch`), `api/siteUrls.ts` (DMS/Vahan/Insurance bases from server `.env`), `api/uploads.ts`, `api/aiReaderQueue.ts`, `api/bulkLoads.ts`, `api/fillDms.ts`, `api/addSales.ts` (Create Invoice eligibility; planned: staging helpers per **§2.2a**), `api/submitInfo.ts`, `api/rtoPaymentDetails.ts`, `api/customerSearch.ts`, `api/admin.ts` — microservice-friendly; swap base URL per env.
- **Hooks:** `useToday`, `useUploadScans`, `useAiReaderQueue` — reusable, testable.
- **Types:** `types/index.ts` — `Page`, `AddSalesStep`, `AiReaderQueueItem`, `ExtractedVehicleDetails`, `PrintForm20Response`, etc.

### 1.1 Key Components

| Component | Purpose |
|-----------|---------|
| `App` | Root; composes `AppLayout` and page by route. |
| `AppLayout` | Header + Sidebar + main slot. |
| `Header` | Dealer name (centre), right slot (e.g. date). |
| `Sidebar` | Nav links; `onNavigate(page)`. |
| `AddSalesPage` | Add Sales flow: Submit Info writes **draft** **`add_sales_staging`** only (`staging_id`). **Create Invoice** (DMS) uses **`staging_id`** until masters commit; **`POST /fill-dms/dms`** returns **`customer_id`** / **`vehicle_id`** after a successful staging-path run. **Generate Insurance** requires those IDs plus eligibility. **Create Invoice** enables when `GET /add-sales/create-invoice-eligibility` returns `create_invoice_enabled` (no `sales_master` row for resolved vehicle+customer keys, or row with blank `invoice_number`); matching uses **`vehicle_master.raw_frame_num`/`raw_engine_num`** and **`customer_master.mobile_number`** only (**no** `dealer_id`). **Generate Insurance** when a **sales** row exists, **`invoice_recorded`**, and no **`insurance_master`** row for that pair has a non-empty **`policy_num`**. |
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
  services/            # Business logic (UploadService, bulk_job_service, bulk_queue_service, bulk_watcher_service, form20_service, handle_browser_opening, utility_functions, insurance_form_values, insurance_kyc_payloads, fill_hero_dms_service, fill_hero_insurance_service, siebel_dms_playwright, submit_info_service, add_sales_commit_service, rto_payment_service)
  routers/             # health, settings, uploads, ai_reader_queue, bulk_loads, fill_dms, add_sales, submit_info, rto_queue, customer_search, dealers, documents, qr_decode, vision, textract_router
  templates/           # form20_front.html, form20_back.html, form20_page3.html
```

- **Routers:** Thin; call services or repos. Each router can be mounted or split into a separate service later.
- **Services:** Stateless, injectable (e.g. `UploadService(uploads_dir=...)`).
- **Repositories:** Table access only; no business rules. **`form_dms.py`** returns the DMS fill row via **inline SQL** (no `form_dms_view`). **`form_vahan.py`** reads **`form_vahan_view`** for Vahan automation and inspection.

### 2.2 API Endpoints (Current)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness. |
| GET | `/settings/site-urls` | Returns `dms_base_url`, `dms_mode`, `dms_real_siebel`, `dms_real_contact_url_configured`, `vahan_base_url`, `insurance_base_url` from `backend/.env` (required at server startup; used by the client for Create Invoice (DMS) and messaging; no in-code URL fallbacks). |
| POST | `/uploads/scans` | Upload scans; enqueue to ai_reader_queue. |
| POST | `/uploads/scans-v2` | Add Sales v2 upload; server runs `OcrService.process_uploaded_subfolder` in the same request and returns `extraction` with `section_timings_ms` including **per-process** fields: `aadhar_textract_front_ms`, `aadhar_textract_back_ms`, `detail_sheet_textract_ms`, optional `aws_textract_prefetch_ms` (when parallel Textract prefetch is enabled), plus phase/total keys. Server **logs** Aadhaar/Details timings at INFO (`ocr_upload_process_timings`). **Aadhaar:** **AWS Textract only** on front/back (no UIDAI QR decode in this path; no Tesseract on Aadhaar). **Details:** raster/PDF uses **AnalyzeDocument FORMS**; `.docx` parsed locally. Optional AWS Textract prefetch, then parallel Aadhaar assembly + Details compile, then merge. |
| GET | `/ai-reader-queue` | List queue items (limit=200). |
| POST | `/ai-reader-queue/process-next` | Process oldest queued item with Tesseract. |
| GET | `/ai-reader-queue/extractions` | List queue items with extracted text. |
| GET | `/ai-reader-queue/extracted-details` | Get extracted details for subfolder. |
| GET | `/ai-reader-queue/insurance-extraction` | Insurance extraction. |
| GET | `/ai-reader-queue/process-status` | Process status. |
| POST | `/ai-reader-queue/empty` | Empty queue. |
| POST | `/ai-reader-queue/process-all` | Process all. |
| POST | `/ai-reader-queue/{item_id}/reprocess` | Reprocess item. |
| POST | `/submit-info` | Validate; insert or update **draft** **`add_sales_staging`** only (optional body **`staging_id`** updates that draft when **`dealer_id`** matches). Response: **`ok`**, **`staging_id`**. Masters commit after successful **Create Invoice** (`add_sales_commit_service`). |
| GET | `/add-sales/create-invoice-eligibility` | Query: `chassis_num`, `engine_num`, `mobile` (digits → `customer_master.mobile_number`). **No** `dealer_id`. Resolves `vehicle_id` / `customer_id` by raw chassis+engine and mobile; then evaluates `sales_master` / `insurance_master` as documented in the router. Returns `create_invoice_enabled`, `matched_sales_row`, `invoice_number`, `reason`, `invoice_recorded`, `generate_insurance_enabled`, `generate_insurance_reason`. |
| GET | `/bulk-loads` | List bulk dashboard rows from hot `bulk_loads` only. |
| GET | `/bulk-loads/counts` | Bulk tab counts from hot data only; `Error` and `Rejected` exclude `action_taken=true`. |
| GET | `/bulk-loads/pending-count` | Count unresolved `Error` + `Rejected` hot rows for the nav badge. |
| PATCH | `/bulk-loads/{bulk_load_id}/action-taken` | Mark an `Error` or `Rejected` row corrected. |
| POST | `/bulk-loads/{bulk_load_id}/prepare-reprocess` | Copy error artifacts back to uploads and start OCR for manual retry. |
| PATCH | `/bulk-loads/{bulk_load_id}/mark-success` | Mark a manually completed error as success. |
| GET | `/bulk-loads/folder/{folder_path}/list` | List files in a bulk result folder. |
| GET | `/bulk-loads/file/{file_path}` | Download or preview a file from a bulk result folder. |
| POST | `/fill-dms` | Full DMS Playwright flow (Add Sales UI: **Create Invoice**); reuses already open logged-in DMS/Vahan tabs when detectable, otherwise auto-opens Edge/Chrome and returns first-time-login guidance. Response includes `dms_milestones` (checklist labels) and, in real Siebel mode, `dms_step_messages` (ordered operator-facing sentences — Add Sales banner prefers these when non-empty). |
| POST | `/fill-dms/dms` | DMS only; body may include **`staging_id`** (`add_sales_staging` UUID, **draft** or **committed**) **or** **`customer_id` + `vehicle_id`**. Staging path: fill values from **`payload_json`** + Siebel scrape only — **no** master reads for fill; on success, **`add_sales_commit_service`** upserts **`customer_master` / `vehicle_master` / `sales_master`**, marks staging **committed**, returns **`customer_id`** / **`vehicle_id`** in the JSON response. Legacy path: `form_dms.py` join. Response also: `dms_milestones`, `dms_step_messages` (real Siebel). |
| GET | `/fill-dms/data-from-dms` | Get data from DMS.txt. |
| GET | `/fill-dms/form20-status` | Form 20 template status. |
| POST | `/fill-dms/print-form20` | Generate Form 20.pdf. |
| POST | `/fill-dms/vahan` | Vahan (RTO) only; tab reuse / auto-open as above. **Playwright fill is not implemented** for production VAHAN (returns **NotImplementedError** until extended). |
| POST | `/fill-dms/insurance` | Insurance only; reuses an already open logged-in Insurance tab when detectable, otherwise auto-opens Edge/Chrome and asks operator to login first-time then retry. Fills proposal fields, scrapes preview, **INSERT**s ``insurance_master`` for the current year (**fails** on duplicate ``customer_id``/``vehicle_id``/``insurance_year``), clicks **Issue Policy**, scrapes again, **UPDATE**s ``policy_num`` / ``insurance_cost``. **Add Sales** passes ``staging_id`` so ``build_insurance_fill_values`` merges ``add_sales_staging.payload_json`` with ``form_insurance_view`` (**BR-20**). Selectors include optional ``#ins-preview-*`` when the portal exposes them. |
| POST | `/fill-dms/insurance/hero` | **Hero Insurance** — ``pre_process``: open ``INSURANCE_BASE_URL``; Sign In → 2W → New Policy; insurer / OVD / mobile / consent; KYC **Proceed** or uploads. ``main_process``: inputs from ``form_insurance_view`` merged with ``payload_json`` when ``staging_id`` is set (**BR-20**); proposal defaults (email, add-ons, CPA, HDFC, reg. date) **hardcoded** in Playwright. On **successful** ``main_process``, same **``insurance_master`` INSERT + Issue Policy scrape + UPDATE** as ``/fill-dms/insurance``. Request: optional ``insurance_base_url``, ``customer_id``, ``vehicle_id``, ``subfolder``, ``dealer_id``, ``staging_id``. Response: ``page_url``, ``login_url``, ``match_base``. |
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

### 2.2a Add Sales staging (deferred commit — design decision)

**Goal:** Hold validated OCR/operator data **server-side** until **Create Invoice** (DMS) completes successfully, then persist `customer_master` → `vehicle_master` → `sales_master` in order (insurance masters deferred to **Generate Insurance** success per product plan). This avoids relying on browser `sessionStorage` across long Playwright runs.

**Ordered commit wave — `service_reminders_queue`:** Treat reminder rows as a **logical fourth step** after `sales_master` is written: they are **not** a separate application INSERT. **`trg_sales_master_sync_service_reminders`** (AFTER INSERT OR UPDATE on `sales_master`) is the **sole** writer to `service_reminders_queue` when `dealer_ref.auto_sms_reminders = 'Y'`. Backend services and repositories must **not** INSERT or UPDATE that table.

**Chosen mechanism: PostgreSQL `add_sales_staging` + UUID `staging_id`**

| Option | Verdict |
|--------|---------|
| **In-process memory only** | Rejected: lost on worker restart; awkward for multi-worker deployments. |
| **Redis (or similar) blob store** | Rejected for v1: no Redis in the current stack; adds ops and failure modes without a driving requirement. |
| **Postgres row (`add_sales_staging`)** | **Selected:** durable, auditable, same DB as masters; aligns with patterns like `bulk_loads` / `rto_queue.processing_session_id` (opaque id + persisted state). |

**Table (executable script `DDL/alter/13a_add_sales_staging.sql`):** `staging_id` UUID PK (generated on first insert; returned by **`POST /submit-info`**), `dealer_id` FK → `dealer_ref`, `payload_json` JSONB (merged **customer**, **vehicle**, **insurance**, **`file_location`**, plus resolved **`customer_id`** / **`vehicle_id`** / **`dealer_id`** for traceability — same logical shape as **`POST /submit-info`**), `status` ∈ `draft` | `committed` | `abandoned`, `created_at` / `updated_at`, optional `expires_at` for TTL cleanup jobs. No signed JWT required for v1; the UUID is unguessable and dealer-scoped validation applies on read.

**API contract: Submit (staging) vs Create Invoice**

| Step | Method / path (planned) | Request | Response / behavior |
|------|-------------------------|---------|---------------------|
| **Submit + draft staging (live)** | **`POST /submit-info`** | `customer`, `vehicle`, `insurance`, `dealer_id`, `file_location`, optional **`staging_id`**. | **`200`:** `{ "ok": true, "staging_id" }`. INSERT/UPDATE **`add_sales_staging`** (`status = draft`) only; masters written after DMS success. |
| **Validate + merge to staging** | `POST /add-sales/staging` *(planned)* | Same JSON body as **`POST /submit-info`** today (`customer`, `vehicle`, `insurance`, `dealer_id`, `file_location`). | **`201`:** `{ "staging_id": "<uuid>", "ready_for_automation": true }`. Runs the same validation and transforms as Submit (e.g. `enrich_customer_address_from_freeform`, subfolder / name checks) but **does not** INSERT/UPDATE `customer_master`, `vehicle_master`, `sales_master`, or `insurance_master`. **`400`** with field errors when validation fails (`ready_for_automation` omitted or false). |
| **Replace staging snapshot** | `PUT /add-sales/staging/{staging_id}` | Same body shape; `staging_id` must exist, same `dealer_id` as stored row, `status = draft`. | Updated `payload_json`, refreshed `updated_at`. |
| **Create Invoice (DMS)** | `POST /fill-dms` / `POST /fill-dms/dms` | **Either** legacy: `customer_id` + `vehicle_id` + `subfolder` + `dealer_id` **or** staging: `staging_id` + `subfolder` + `dealer_id` (no real IDs until first commit wave). | Build DMS fill values from **`add_sales_staging.payload_json`** (OCR merge) or, until wired, from **`form_dms.py`** inline join over masters — **not** from **`form_dms_view`** (dropped). **`form_insurance_view`** is unrelated to DMS fill; **Generate Insurance** reads the view after sale rows exist and merges the same **`staging_id`**’s **`payload_json`** for sparse insurer/nominee (**BR-20**). On **automation success**, run ordered commits and set staging `status = committed`; response includes **`customer_id`** and **`vehicle_id`** for downstream **Print Forms**, **Generate Insurance**, RTO queue. |
| **Eligibility** | `GET /add-sales/create-invoice-eligibility` | `chassis_num`, `engine_num`, `mobile` — natural keys only (no `dealer_id`). |

**Rollout:** Add Sales client passes **`staging_id`** from Submit Info into **Create Invoice** (`/fill-dms` / `/fill-dms/dms`) and into **Generate Insurance** (`/fill-dms/insurance`, **`/fill-dms/insurance/hero`**) so **`form_insurance_view`** and **`payload_json`** together supply insurance automation inputs (**BR-20**). **`POST /add-sales/staging`** remains optional for a future staging-only Submit path.

### 2.3 Tesseract OCR Reader

- **Service:** `OcrService` in `services/ocr_service.py` — processes one queue item at a time (oldest first), runs Tesseract on the scan file under `UPLOADS_DIR/<subfolder>/<filename>`, writes extracted text to `OCR_OUTPUT_DIR` as `<subfolder>_<filename>.txt`.
- **Config:** `config.OCR_OUTPUT_DIR` (default: `backend/ocr_output`). Tesseract binary must be on system PATH (or set `pytesseract.pytesseract.tesseract_cmd`). `config.OCR_LANG` (default: `eng+hin`) for English + Hindi; see **Documentation/tesseract-ocr-setup.md** for installing Hindi tessdata for Aadhar scans.
- **Queue status:** `queued` → `processing` → `done` or `failed`.
- **Aadhaar (Add Sales / queue):** `OcrService._process_aadhar` and upload-time `_pipeline_merge_aadhar_customer` use **AWS Textract** on **`Aadhar.jpg`** (and on **`Aadhar_back.jpg`** when geo fields are still weak). Parsed customer fields merge into **existing** JSON `customer` via `_merge_qr_customer_into_existing` (name is historical; it fills blanks from the Aadhaar fragment). **No UIDAI QR decode** runs in scans-v2 or `get_extracted_details`. Ad-hoc QR decode remains on **POST `/qr-decode`** (`qr_decode_service`). **`get_extracted_details`** applies **Textract fallback (Raw_OCR):** after `Raw_OCR.txt` is built (`--- Aadhar.jpg ---` / `--- Aadhar_back.jpg ---` sections), `OcrService` parses that text — **DOB** from labeled lines, **`/DB:`** / **`DB:`** (mis-OCR of DOB) before a slash date, and a marker-proximity pass that prefers **`dd/mm/yyyy`** near DOB/DB tokens (slash density in the local window); (avoiding **issued** dates when possible); **gender** via **DOB anchor** (after `dd/mm/yyyy`, skip one token, next `/`, gender token) plus **`Gender:`** / **Sex/** / **yes/** fallbacks; **address** from back (including `Address` + newline + `C/O:` / **`S/O:`** + `Near …` without stopping the `Address:` block at relation lines). `customer_address_infer.normalize_address_freeform` sets **`care_of`** as **`C/o`/`S/o`/`W/o`/`D/o` + name**, strips that clause from the body, **prepends** it to the composed **`address`**, and uses **`DIST: district, state - PIN`**, **comma-separated clauses** with a **trailing dash run** before the last 6-digit **PIN**, and trailing **`<state> - <PIN>`**; known Indian states/UTs for **state** / **PIN**. Applied at end of `process_uploaded_subfolder`, again in `get_extracted_details` when `Raw_OCR.txt` exists. If **gender** is still empty after those steps (and Aadhaar uploads exist), `OcrService` sets **`customer.gender` = `Male`** (`_default_gender_male_if_unread`). Constructed `address` uses POA-style parts when present; when merging into an existing customer, a non-empty Details `address` is **not** overwritten by a shorter composed line.
- **Sales detail template mapping:** Details-sheet extraction also supports the A5 Sales Detail Sheet label style (e.g., `Full Name`, `Mobile Number`, `Aadhaar Number`, `Profession`, `Marital Status`, `Nominee ...`, `Financier Name`) and merges these into `OCR_To_be_Used.json` (`customer` + `insurance`) for Add Sales auto-population.
- **Add Sales v2 `Details.jpg`:** The file is always stored under that name; content is detected by magic bytes (JPEG/PNG/PDF or ZIP-based `.docx`), so Word exports mislabeled as `.jpg` still use the docx parser instead of Textract on invalid bytes.
- **Vehicle fields from Details:** Textract FORMS keys are mapped with normalized labels (including `Chassis Number`, `Engine Number`, etc.). If pairs are sparse on a PDF, `full_text` is parsed for the same labels (e.g. `Chassis Number: … Engine Number: …` on one line).

### 2.4 Form 20 Generation

- **Service:** `form20_service.py` — `generate_form20_pdfs(subfolder, customer, vehicle, vehicle_id, dealer_id)`.
- **Flow:** Prefer Word template (`templates/word/FORM 20 Template.docx`) → fill placeholders → convert to PDF (docx2pdf or LibreOffice) → output `Form 20.pdf` (all pages). Fallback: PDF overlay on Official FORM-20 or separate templates. Fallback: HTML templates. Override via env `FORM20_TEMPLATE_DOCX`.
- **Gate Pass:** Word template (`templates/word/Gate Pass Template.docx`) → fill placeholders → convert to PDF → output `Gate Pass.pdf`. Placeholders: `{{field_0_today_date}}`, `{{field_1_oem_name}}`, `{{field_2_customer_name}}`, `{{field_3_aadhar_id}}`, `{{field_4_model}}`, `{{field_5_color}}`, `{{field_6_key_num}}`, `{{field_7_chassis_num}}`. Override via env `GATE_PASS_TEMPLATE_DOCX`.
- **Placeholders:** `{{field_0_city}}`, `{{field_1_name}}`, `{{field_2_care_of}}`, `{{field_3_address}}`, `{{field_10_dealer_name}}`, `{{field_14_body_type}}`, `{{field_16_oem_name}}`, `{{field_17_year_of_mfg}}`, `{{field_20_cubic_capacity}}`, `{{field_21_model}}`, `{{field_22_chassis_no}}`, etc.

### 2.4a VAHAN (Playwright)

- **Static training HTML** under `dummy-sites/vaahan/` and Playwright that drove it **were removed**. Set **`VAHAN_BASE_URL`** in **`backend/.env`** to the **production** VAHAN portal. **`run_fill_vahan_only`**, **`run_fill_vahan_batch_row`**, and **`_fill_vahan_and_scrape`** raise **`NotImplementedError`** until production selectors exist. **`run_rto_pay`** in **`rto_payment_service.py`** returns a clear error (same reason).

### 2.4b DMS (Playwright)

- **Fill DMS** is **Siebel only**: **`DMS_MODE`** defaults to **`real`**; **`dummy`** is **rejected** at server startup. **`run_fill_dms_only`** calls **`_run_fill_dms_real_siebel_playwright`** → **`siebel_dms_playwright.run_hero_siebel_dms_flow`**. Set **`DMS_BASE_URL`** to your Hero Connect entry and **`DMS_REAL_URL_CONTACT`** (full GotoView) plus optional **`DMS_REAL_URL_*`** — see **`backend/.env.example`**.
- **Address → State / PIN / Care of:** `customer_address_infer` parses **`C/O:`**, **`S/o:`**, **`W/o:`**, **`D/o:`** into **`care_of`** and **`DIST: …`** into **city/state/PIN**; **`enrich_customer_address_from_freeform`** merges into Submit Info. **`fill_hero_dms_service._build_dms_fill_values`** uses the same enrichment when the DB row is sparse.
- **`skip_find` in DB does not bypass Contact Find** on real Siebel — see **§2.4d**.

### 2.4c Insurance (Playwright)

- **`INSURANCE_BASE_URL`** must be the **real** insurer portal (e.g. Hero MISP). Repo static insurance HTML and **`/dummy-insurance`** mounts **were removed**. Automation: **`form_insurance_view`** + optional **`add_sales_staging.payload_json`** (**`build_insurance_fill_values`**, **BR-20**); KYC / policy selectors as implemented in **`fill_hero_insurance_service.py`**; **`insert_insurance_master_after_gi`**, **`click_issue_policy_and_scrape_preview`**, **`update_insurance_master_policy_after_issue`**; writes **`Insurance_Form_Values.txt`**.

### 2.4d Real Siebel DMS — BRD §6.1a checklist vs Playwright

**Source of truth for intended steps:** `Documentation/business-requirements-document.md` **§6.1a**. **Code:** `backend/app/services/siebel_dms_playwright.py` (`run_hero_siebel_dms_flow`, `_add_enquiry_opportunity` when contact search has no table rows, nested `stage_5_vehicle_flow` for vehicle + In-Transit), `backend/app/services/fill_hero_dms_service.py` (`_run_fill_dms_real_siebel_playwright`, `run_fill_dms_only`, **`aadhar_id`** from `customer_master.aadhar` in **`_build_dms_fill_values`**). **Operator trace:** each real **`/fill-dms/dms`** run overwrites `ocr_output/<dealer_id>/<subfolder>/Playwright_DMS.txt` with a live UTC log (values used, STEP/NOTE/MILESTONE, **`[FORM]`** lines: `siebel_step`, form/screen label, action, field=value pairs, DECISIONs, `[END]` + error). Template folder `ocr_output/dealer/mobile_ddmmyyyy/` only explains this (no static SOP copy). When **`SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`** is True, automation runs the video path: **Find→Contact→mobile + exact First Name→Go**; grid match uses **`_siebel_ui_suggests_contact_match_mobile_first`**. **Open enquiry** = **Contact_Enquiry** subgrid has ≥1 populated row; **`_contact_find_title_sweep_for_enquiry`** drills **Title** rows matching mobile+first in order until one has an enquiry, else **Add Enquiry** with **`first_name` + `.`…** (extend dots on conflict) and re-find/drill. **`_add_enquiry_opportunity`**: Enquiry# must **change** from pre-**Ctrl+S** at **0.5s / 2.5s / 3.5s** post-save or hard fail (logged). The full §6.1a linear stage chain runs only when that flag is False.

| BRD §6.1a step | Intended Siebel action | Playwright (`DMS_MODE=real`, …) |
|----------------|------------------------|-----------------------------------|
| 0 | Logged-in session | Operator/CDP; `_get_or_open_site_page` (no scripted Hero login) |
| 1 | Find contact by mobile | **Always** Find→Contact, **mobile only**, **Go** (`skip_find` in DB does not bypass) |
| 2a | New customer: basic enquiry + re-find + care | **No table match** → try **`_add_enquiry_opportunity`** (**Find→Vehicles**, VIN fly-in + scrape; then **Enquiry**, **Opportunities List:New** — contact/name/mobile/landline/UIN/address/**City**/pin, model/color from scrape, **Finance Required** from DB, **Booking Order Type** Normal Booking, **Enquiry Source** / **Point of Contact** walk-in, **Actual Enquiry Date**; **Financier** fields not automated; Ctrl+S; **`(ok, detail)`** on failure); on failure → **`_fill_basic_enquiry_details`** + **Save**; **`new_enquiry`** path still uses basic enquiry; mandatory **`_refind_customer_after_enquiry`** when a new enquiry was created; then care-of + **Save**; milestone **Add enquiry saved** / **Enquiry created** |
| 2b | Existing: skip basic + care always | **Heuristic:** `_siebel_ui_suggests_contact_match` (table row, ≥3 **`td`**) → skip stages 2–3; **always** stage 4 care-of + **Save**; milestones **Customer found**, **Care of filled** |
| 3 | Vehicle search; **In Transit** vs other | **`scrape_siebel_vehicle_row`** sets **`in_transit`** if grid text matches `in transit` |
| 4a | Receipt; **Pre Check** / **PDI** only on **dealer** stock | If **`in_transit`:** receipt URL → **Process Receipt** (`prepare_vehicle`); **no** GotoView URL automation for Pre-check/PDI. **Dealer** (`in_transit` false): **Serial Number** → **`_siebel_run_vehicle_serial_detail_precheck_pdi`** (tab Pre-check + PDI) + Features; milestones as implemented in **`prepare_vehicle`** / **`_attach_vehicle_to_bkg`** |
| 4b | Booking + allocate | **Generate Booking** **after** vehicle for **both** branches; if **not** `in_transit`: then `goto` **`DMS_REAL_URL_LINE_ITEMS`**, **Price All** (optional), **Allocate** / **Allocate All** |
| `skip_find` | Enquiry without Find | **Ignored** for real automation: always **Find→Contact** first (even if DB says `skip_find`), then linear SOP; **Generate Booking** after vehicle (**always**); allotment when **not** In Transit |
| BR-16 | No **Create Invoice** | Compliant; `_requires_operator_create_invoice` may still block if UI demands operator |
| Milestones | — | **Booking generated**, **Allotment view opened**, **Vehicle allocated** (not “Invoice created”) |
| — | Browser left open | `_KEEP_OPEN_BROWSERS` / CDP |

**Residual gaps / tuning:** Contact match and **In Transit** are **heuristic** (tenant grid/layout may need selector or copy tweaks). **Process Receipt**, **PDI Submit**, and **Allocate** use toolbar name patterns; dialogs/OTP/exchange/finance are **not** automated. **Reports** URL is not auto-opened.

- **Create order (video SOP path):** After Ctrl+S on a new **Sales Orders** booking, **`_attach_vehicle_to_bkg`** clicks the header drill-down **`a[name='Order Number'][tabindex='-1']`** (fallback: `a[name='Order Number']`). Failure surfaces as **`create_order`** error; **`order_drilldown_opened`** is set on the scrape dict when successful.

- **`Playwright_DMS.txt` vehicle visibility:** **Add Enquiry** and stage-5 prep scrapes **`full_chassis`** / **`full_engine`** (and model/color) from Siebel **Vehicle Information** / grid after VIN drill-in — not echoed as separate “from_source” header lines. **Stage 5** runs **`prepare_vehicle`**: **Auto Vehicle List** **Find→Vehicles** (``*``VIN/``*``Engine partials only — **LLD** **6.51**) + grid scrape, left **Search Results** VIN hit (**`_siebel_try_click_vin_search_hit_link`**), **`_siebel_fill_key_battery_from_dms_values`**, aria-label **Vehicle Information** merge, **Inventory Location** gate; **dealer** stock (`in_transit` false) → **Serial Number** → **`_siebel_run_vehicle_serial_detail_precheck_pdi`** → **Features and Image**; **in-transit** stock → no tab Pre-check/PDI, optional receipt URL + **Process Receipt** only (no GotoView Pre-check/PDI URL flow in **`prepare_vehicle`**). Post-booking **`_attach_vehicle_to_bkg`** still uses the shared tab helper after line-item **VIN** and **Serial Number**. Grid keys differ from **`full_chassis`** / **`full_engine`**; notes in the trace state this distinction.

- **Temporary navigation override (real Siebel `create_order`):** `backend/app/services/siebel_dms_playwright.py` currently contains a hardcoded comparison `mobile_number == "8952897358"` to force the alternate **Find → Vehicle Sales** navigation branch during tenant-specific debugging. When this condition matches, automation directly attempts to open `Order#` by double-click; otherwise it takes the `Sales Orders List:New (+)` path first, then opens `Order#`.

### 2.5 Database Access

- **Connection:** `get_connection()` in `db.py` using `DATABASE_URL`.
- **Insurance Playwright tuning (optional `.env`):** `INSURANCE_ACTION_TIMEOUT_MS` (default 5500) for KYC/navigation actions; `INSURANCE_POLICY_FILL_TIMEOUT_MS` (default 3200) while filling the policy / insurance-details form. Lower values speed up local runs; raise if a slow portal flakes.
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
| *(DMS fill row)* | Implemented in **`form_dms.py`**; **`form_dms_view`** removed (**`13b_drop_form_dms_view.sql`**). |
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
| 1.1 | Mar 2026 | — | Extended dummy DMS Playwright flow (enquiry/stock/PDI/allocate/line-items) and ex-showroom → `vehicle_price` contract |
| 1.2 | Mar 2026 | — | Aadhaar front Textract/Tesseract fallback: gender from **yes/ MALE** (mis-OCR of **Sex / Male**) when QR is unavailable |
| 1.3 | Mar 2026 | — | Aadhaar back / freeform address: **DIST** line with double-dash PIN separators; trailing **state + PIN** without **DIST**; **`Address:`** OCR block includes following **C/O** line |
| 1.4 | Mar 2026 | — | Aadhaar OCR: gender from **DOB anchor** (skip word, next `/`, gender token); state/PIN from **comma segments + dash runs** before last 6-digit PIN |
| 1.5 | Mar 2026 | — | DMS: ``DMS_MODE`` / ``DMS_REAL_URL_*`` for Hero Connect Siebel navigation branch; ``GET /settings/site-urls`` exposes mode flags |
| 1.6 | Mar 2026 | — | Playwright-managed Edge/Chrome: ``--remote-debugging-port`` via ``PLAYWRIGHT_MANAGED_REMOTE_DEBUG_PORT`` (default 9333); CDP candidate list includes that port |
| 1.7 | Mar 2026 | — | DMS tab detection: host+path prefix match for Siebel query URLs (``SWECmd=Login``, ``GotoView``); logs when no CDP session exists |
| 1.8 | Mar 2026 | — | **§2.4d** BRD §6.1a vs Playwright parity (real Siebel + dummy); **§2.4b** order note; **API** `/fill-dms/dms` cross-references BRD/LLD |
| 1.9 | Mar 2026 | — | Real Siebel `run_hero_siebel_dms_flow` implements §6.1a; **§2.4d** table refreshed; **DMS_MILESTONE_ORDER** adds Booking/Allotment/Allocate labels |
| 2.0 | Mar 2026 | — | **Pre Check** before PDI (`DMS_REAL_URL_PRECHECK`, combined PDI URL); **§6.1a** / **§2.4d** updated |
| 2.1 | Mar 2026 | — | **POST /uploads/scans-v2** — `extraction.section_timings_ms`; parallel Aadhaar+Details compile; QR-first + Textract fallbacks (`ocr_service`) |
| 2.2 | Mar 2026 | — | Aadhaar OCR: **Textract only** (removed Tesseract on Aadhaar); §2.3 Aadhaar back narrative updated |
| 2.3 | Mar 2026 | — | scans-v2 Aadhaar: **no UIDAI QR** in pipeline; `section_timings_ms` uses **`aws_textract_prefetch_ms`** (no QR timing keys); §2.3 Aadhaar bullet rewritten |
| 2.4 | Mar 2026 | — | Aadhaar front: **`/DB:`** / **`DB:`** DOB patterns + marker/slash heuristic; back: **`care_of`** = **`S/o`/`W/o`/`D/o`/`C/o` + name** and prepended to **`address`** in `normalize_address_freeform` |
| 2.5 | Mar 2026 | — | Real Siebel: contact match = **table rows only**; **`dms_siebel_forms_filled`** requires **Save** + vehicle step OK; **PDI** clicks avoid bare **Submit**; §2.4b/§2.4d + `technical-architecture` Bugbot note |
| 2.6 | Mar 2026 | — | Real Siebel **linear SOP**: basic enquiry vs care-of split; mandatory **re-find** after new enquiry; **Generate Booking** after vehicle for all; allotment after booking (non-transit); invoice hook (message only) |
| 2.7 | Mar 2026 | — | Siebel: nested **`stage_5_vehicle_flow`**; **`Playwright_DMS.txt`** at `ocr_output/dealer/mobile_ddmmyyyy/`; **§2.4d** `skip_find` row aligned with booking-after-vehicle |
| 2.8 | Mar 2026 | — | Add Sales: no upload timing suffix; clear stale DMS banner on new upload |
| 2.9 | Mar 2026 | — | **`Playwright_DMS.txt`** = runtime execution log (overwrite per run); Add Sales clears Fill DMS error + banner when tab visible again after hidden **only if** the last Fill DMS ended with error/warning |
| 3.0 | Mar 2026 | — | Real Siebel: **`skip_find`** in `dms_contact_path` **ignored** — always Stage 1 Contact Find first (**§2.4d** + `fill_dms_service` docstring) |
| 3.1 | Mar 2026 | — | Playwright: **never** `Browser.close()` / `Playwright.stop()` on API exit or thread switch; retain-list prevents GC closes; RTO payment dummy flow leaves Edge open |
| 3.2 | Mar 2026 | — | **`Playwright_DMS.txt`**: **`[FORM]`** trace per SOP-ish step (screen, action, values); **`form_trace`** wired through vehicle scrape + pre-check/PDI helpers (**§2.4d**) |
| 3.3 | Mar 2026 | — | Real Siebel: optional **`SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`** in `siebel_dms_playwright.py` — video **Find Contact Enquiry** path only (Find → drill → **All Enquiries**), then return; milestone **All Enquiries opened** |
| 3.4 | Mar 2026 | — | Fill DMS: **`_install_playwright_js_dialog_handler`** on the reused tab — avoids Playwright Node **ProtocolError** (*Page.handleJavaScriptDialog: No dialog is showing*) when Siebel closes JS dialogs before default CDP dismiss |
| 3.5 | Mar 2026 | — | Video SOP drill-down: **`_siebel_try_click_mobile_search_hit_link`** searches **chained iframes**, spaced/dashed phone text, ``table tr`` / **role=row**, row-click fallback; longer settle after Find/Go |
| 3.6 | Mar 2026 | — | Search Results pane: target **`javascript:void(0)`** drilldown + **`.siebui-applet`** scope; normal / **force** / **dblclick** sequence for Siebel left list |
| 3.7 | Mar 2026 | — | Find fly-in: **`_click_find_go_query`** scopes to applet with **Mobile Phone**; **`title`/`aria-label`**, Siebel classes, **`get_by_title(Find)`**, svg icon buttons with Find/Go tooltip |
| 3.8 | Mar 2026 | — | Find path hardening: **`_try_prepare_find_contact_applet`** now explicitly selects **Contact** in the top global finder (dropdown showing **Find**) before filling mobile and firing query |
| 3.9 | Mar 2026 | — | Find applet reliability: `_contact_view_find_by_mobile` second-pass retry forces Find fly-in reopen + Find→Contact reselection before mobile fill; global finder now mirrors operator flow **Find → Contact** |
| 4.0 | Mar 2026 | — | Stage-1 find now tries strict applet-scoped path first: `_try_fill_mobile_and_find_in_contact_applet` fills Mobile Phone and clicks local Find icon in the opened **Find→Contact** applet before any page-wide fallback |
| 4.1 | Mar 2026 | — | Naming modularization: main Hero flow renamed to **`Playwright_Hero_DMS_fill`** (legacy alias kept), stage-1 subprocess renamed to **`find_customer`** for reusable OEM-specific module design |
| 4.2 | Mar 2026 | — | Existing customer open-record behavior: after stage-1 match, click left Search Results customer hit, then click right Contacts applet first-name drilldown (e.g., **Akash**) via `_siebel_open_found_customer_record` |
| 4.3 | Mar 2026 | — | Post-find modularity: actions after `find_customer` are centralized in `fill_father_name(...)`, including existing-customer record open (left hit + right first-name drilldown) and father/relation update |
| 4.4 | Mar 2026 | — | Video SOP (`SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES=true`) now follows found-customer path: left hit → click right `First Name` → parse `care_of` to `S/O`/`W/O`/`D/O` + relation name and fill those fields, then stop |
| 4.5 | Mar 2026 | — | Relation-name derivation updated: when fallback value is derived, prefix uses gender rule (`S/o` for male, else `D/o`); `gender` passed in DMS values and through Hero Playwright flow |
| 4.6 | Mar 2026 | — | Real Siebel: **no contact table match** after Find/Go → **`_add_enquiry_opportunity`** (vehicle list, **Opportunities List:New**, **Finance Required** Y/N from **`financier_name`**, **Booking Order Type** Normal Booking, **UIN** from **`aadhar_id`** last 4, **Point of Contact** Customer Walk-in, Ctrl+S); **`fill_dms_service`** adds **`aadhar_id`** to DMS values |
| 4.7 | Mar 2026 | — | **`_add_enquiry_opportunity`**: after chassis/engine query, **`scrape_siebel_vehicle_row`** must yield **model**, **year_of_mfg**, and **color** before Enquiry tab / **Opportunities List:New**; merged into **`out['vehicle']`** on success |
| 4.8 | Mar 2026 | — | Add-enquiry vehicle step: **`_try_prepare_find_vehicles_applet`**, **`_try_fill_vin_engine_in_vehicles_find_applet`** (Hero **Find→Vehicles** + `*` wildcards + Enter); **`_merge_scrape_vehicle_detail_applet`** fills model/year/color from **Vehicle Information** when the list grid is narrow |
| 4.9 | Mar 2026 | — | After vehicle query: left **Search Results** VIN link (**Siebel Find** tab), then **`_merge_scrape_vehicle_record_from_vin_aria`** (anchor `aria-label`/`title` **VIN**) → **`full_chassis`**, **`full_engine`**, model, **Dispatch Year** → `year_of_mfg`, color; merged into **`out['vehicle']`**; **Data from DMS.txt** includes full VIN/engine lines when present |
| 5.0 | Mar 2026 | — | Add-enquiry: **`year_of_mfg`** normalized to **YYYY**; add-enquiry merge omits **frame_num** / **engine_num**; **Enquiry** tab prefers **`aria-label="Enquiry Selected"`** then role/name **Enquiry Selected**, then plain **Enquiry** |
| 5.1 | Mar 2026 | — | **`_normalize_manufacturing_year_yyyy`**: strip digit grouping (comma / NBSP / thin space) so Siebel values like **2,009** map to **2009**; avoids empty year and add-enquiry gate failure |
| 5.2 | Mar 2026 | — | **`_add_enquiry_opportunity`**: full opportunity form (contact, mobile, landline, UIN, address, city, optional district/tehsil/age/gender, model/color from scrape, finance Y/N, booking type, walk-in source/contact, **Actual Enquiry Date** today **dd/mm/yyyy**); **does not** set Financier; returns **`(ok, detail)`**; video SOP **`error`** includes **`detail`**; DMS fill row (historical **`dms_sku`** superseded Mar 2026 — **`15a`**) |
| 5.3 | Mar 2026 | — | **`update_vehicle_master_from_dms`**: maps **full_chassis** / **full_engine**, **dispatch_year** fallback for **year_of_mfg**, **ex_showroom_price** → **`vehicle_ex_showroom_price`**; runs after real Siebel when **`out['vehicle']`** is non-empty (including partial Add Enquiry scrape); **raw_frame_num** / **raw_engine_num** merge removed in **6.6**; **`15a`**: **variant**, **vehicle_type** ALL CAPS, 2W fields, dealer **rto_name**/**oem_name**, partial unique **chassis**, drop **dms_sku** |
| 5.4 | Mar 2026 | — | Address inference: care-of parser accepts `S/O Name` without colon and normalizes to uppercase relation (`S/O`); Add Enquiry `+` click prefers frame-local **`aria-label="Opportunity Form:New"`** before fallback **`Opportunities List:New`** |
| 5.5 | Mar 2026 | — | Add Enquiry new-opportunity click is now **strictly** frame-local **`Opportunity Form:New`** (no **`Opportunities List:New`** fallback); subsequent form detection prefers the same frame to avoid focus shifting outside the pane |
| 5.6 | Mar 2026 | — | Add Enquiry frame-focus hardening: after Enquiry tab click, retry activation of **Opportunity Form** pane in each frame, then click **`Opportunity Form:New`** via exact + contains selectors with bounded retries and settle waits |
| 5.7 | Mar 2026 | — | Add Enquiry form fill now uses strict **frame-scoped** label/input and dropdown selection (`_select_dropdown_by_label_on_frame`) inside the detected **Opportunity Form:New** frame; removed page-wide dropdown fallback during this step to prevent focus drift |
| 5.8 | Mar 2026 | — | Add Enquiry handoff: `full_chassis` / `full_engine` are propagated to client DMS section (mapped to frame/engine display fields), add-enquiry merge leaves `vehicle_price` blank unless actually scraped, and video SOP no-contact path now saves add-enquiry then **re-runs Find→Contact by mobile** to rejoin the normal route |
| 5.9 | Mar 2026 | — | Add Enquiry post-save logging: after Ctrl+S, scrape **Enquiry#** from the same Opportunity form frame (best-effort) and write it to `Playwright_DMS.txt` via `[NOTE]` and `[FORM] add_enquiry_saved` |
| 6.0 | Mar 2026 | — | Add Enquiry required-field hardening: derive **Age** from DB DOB, normalize **Gender** from DB, force **Landline** (fallback = Mobile), set **Email=NA**, set **District** and **Tehsil/Taluka** from City fallback, `Address Line 1` from substring between first/second comma, run **City/Town/Village** pick-search and confirm **OK**, select first **Variant** option, and enforce Enquiry# change after Ctrl+S before continuing |
| 6.1 | Mar 2026 | — | **`_attach_vehicle_to_bkg`** after new-booking save (header **Order Number** link); stage 5 **`[NOTE]`** for grid vs **full_chassis** / **full_engine** (Add Enquiry detail scrape) |
| 6.2 | Mar 2026 | — | **`vehicle_master.vehicle_ex_showroom_price`** (rename from `vehicle_price`, **03j**); **`update_vehicle_master_from_dms`**: **raw_key_num** → **key_num**; **`form_vahan_view.vehicle_price`** alias unchanged |
| 6.3 | Mar 2026 | — | **`sales_master.order_number`** / **`invoice_number`**: Siebel scrape in **`_create_order`** (+ **`Data from DMS.txt`**); **`update_sales_master_from_dms_scrape`** after **`run_fill_dms_only`** |
| 6.4 | Mar 2026 | — | **`sales_master.enquiry_number`** (`05i`); **`vehicle_ex_showroom_cost`** → **`vehicle_ex_showroom_price`** mapping; **`update_sales_master_from_dms_scrape`** now called for real Siebel path (was missing); all DMS scraped values stored to DB |
| 6.5 | Mar 2026 | — | Service module renamed to **`fill_hero_dms_service.py`**; Fill DMS validates **`dealer_ref.oem_id`** (Hero = `1`) before execution; OEM guardrail error for other OEMs |
| 6.6 | Mar 2026 | — | **`update_vehicle_master_from_dms`** no longer updates **`raw_frame_num`** / **`raw_engine_num`** (DMS merge must not overwrite detail-sheet identity used for DMS fill partials / Add Enquiry VIN search) |
| 6.7 | Mar 2026 | — | Video SOP (`SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`): **`_contact_find_title_sweep_for_enquiry`** tries each **Title** row matching the mobile (**in-place** drills; **no** Contact Find between duplicate rows) until **Contact_Enquiry** has data — avoids **Add Enquiry** when a duplicate contact row already has an enquiry |
| 6.8 | Mar 2026 | — | Real Siebel: **Contact First Name** required (validated; placeholders rejected). **Find** uses **mobile + first name** (full linear SOP stage 1 + video SOP + post–basic-enquiry re-find). Title sweep / duplicate drills use **fuzzy** first-name row match + **mobile-only** fallback when needed; suffixed **`first_name`** (`.`, `..`, …) for new enquiry when no open enquiry; **`_add_enquiry_opportunity`** post-save Enquiry# gate (**0.5s / 2.5s / 3.5s**) |
| 6.9 | Mar 2026 | — | **`_contact_enquiry_tab_has_rows`**: treat **`input`/`textarea` `name="Enquiry_"`** (Open UI jqGrid) as source of truth for populated enquiry rows; aggregate max **rowCount** / **enquiryNumber** across frames; **`debug-08e634.log`** **E1** probe per frame |
| 6.10 | Mar 2026 | — | **`_frames_for_enquiry_subgrid_eval`**: evaluate **main_frame** first for **`#jqgh_s_1_l_Enquiry_`** / **`Enquiry_`** after drilldown; short-circuit return when main has **rowCount** > 0; remove invalid **`Page`** object from frame loop; **`_contact_find_title_sweep_for_enquiry`** docstring: per-row drill + **Contact_Enquiry** in duplicate-mobile case |
| 6.11 | Mar 2026 | — | **`_contact_find_title_sweep_for_enquiry`**: ~~**`_refind_before_next_duplicate_row`**~~ removed — duplicate rows use **in-place** drill only; drill **ordinal ≥1** with **`first_name_exact=None`** |
| 6.12 | Mar 2026 | — | **`_contact_find_title_sweep_for_enquiry`**: on failed in-place drill for **ordinal ≥1**, **break** immediately (no second identical drill, no URL/nav parameters on this helper); **`E3b`** NDJSON on that path |
| 6.13 | Mar 2026 | — | **`_contact_enquiry_tab_has_rows`**: Hero **Enquiries** applet — detect **Enquiry#** blue links (e.g. `*-SENQ-*`, hyphenated keys); **`heroLinksInApplet()`** scoped to **`.siebui-applet`** when body text includes **Enquiries** / **Enquiry#**; fallback scan if jqGrid header/id mismatches; **`diag.usedHeroLinkScan`** / **`heroLinkScope`** |
| 6.14 | Mar 2026 | — | Video SOP after enquiry sweep: **`_siebel_try_activate_find_contact_context`** (**Find Contact** tab/link, else **Contacts** list sub-tab) → **`_siebel_open_found_customer_record`** (First Name) even when **care_of** is empty; **Contact ID** scrape extended to **Contacts** grid **Contact Id** column + extra `name`/`id` selectors |
| 6.15 | Mar 2026 | — | **`fill_hero_insurance_service`**: **`pre_process`** / **`main_process`** / **`post_process`** + **`POST /fill-dms/insurance/hero`**: launch URL = **`INSURANCE_BASE_URL`**; **`_get_or_open_site_page(..., launch_url=)`**; origin match + login readiness + auto-fill selectors |
| 6.16 | Mar 2026 | — | **`main_process`**: VIN (**``full_chassis``**) → Submit → **I agree**; proposal form through **Proposal Review**; KYC upload path clicks **Proceed**/Continue after attach; **`post_process`** prefers **`main_result.page_url`** |
| 6.17 | Mar 2026 | — | **`form_insurance_view`**: chassis/customer/nominee/insurer from existing master columns; proposal UI defaults hardcoded; OCR insurer fallback when DB insurer empty |
| 6.18 | Mar 2026 | — | Real Siebel Contact Find: **`_first_name_for_contact_find_query_field`** types **exact** first name (no `*`); **Superseded for post-Find row logic** by **6.21** (fuzzy grid + mobile fallback restored) |
| 6.19 | Mar 2026 | — | **`_create_order`** (Vehicle Sales → Sales Orders New): after **Booking Order Type = Normal Booking**, fills **Comments** with **`Battery is <battery_partial>`** when DMS fill / detail sheet **Battery No.** (`battery_partial`) is non-empty; **`Comment`** label fallback |
| 6.20 | Mar 2026 | — | **`_create_order`**: before **+**, **`_try_vehicle_sales_find_existing_order`** — **Find** (`aria-label` / role) → dropdown **Mobile Phone#** → **Tab** → mobile → **Go**/**Enter**; if list row matches mobile, **`elif`** **`_attach_vehicle_to_bkg`** and **return** (no **+**, no Comments-on-new-form); else existing **+** path |
| 6.21 | Mar 2026 | — | Reverted **6.18** post-Find strictness: **`_siebel_ui_suggests_contact_match_mobile_first`** again uses **fuzzy** first-name + **`rowContainsFindFirstKey`** + **`mobile_only_js`** fallback; **`_contact_mobile_drilldown_plans`** **`row_match_js`** again uses **textMatchesFindFirstName** + **`rowContainsFindFirstKey`**. **Find** field remains exact via **`_first_name_for_contact_find_query_field`** (**BRD §6.1b** / changelog **3.15**) |
| 6.22 | Mar 2026 | — | **`_create_order`** **`_try_vehicle_sales_find_existing_order`**: set Find field type to **Mobile Phone#** via **`#findfieldsbox`** scoped **`select`** / JS option match, else **type-to-select** (`Mobile Phone#` + Enter) on first criteria **`td`** input; legacy **Alt+ArrowDown** loop only if those fail (**LLD** **6.20**) |
| 6.23 | Mar 2026 | — | **`_attach_vehicle_to_bkg`** line items **New**: primary id **`s_1_1_35_0_Ctrl`**; fallback **`s_1_1_35_0`** |
| 6.24 | Mar 2026 | — | **`_attach_vehicle_to_bkg`** line-item **VIN**: **`locator.type`** (not **`page.keyboard`**, for iframe focus) + selectors **`#1_s_1_l_VIN`**, **`input[id$='_l_VIN']`**, **`name=VIN`**; JS fallback scans **`_l_VIN` / `name=VIN`**; settle after **New** before **VIN** |
| 6.25 | Mar 2026 | — | **`_build_dms_fill_values`**: merges **`vehicle_master`** **`chassis`/`engine`/`model`/`colour`** into **`dms_values`** as **`full_chassis`**, **`full_engine`**, **`frame_num`/`engine_num`**, **`model`/`vehicle_model`**, **`color`/`vehicle_colour`** so **`_create_order`** / **`_attach_vehicle_to_bkg`** get full VIN when the DMS fill row only has partials |
| 6.26 | Mar 2026 | — | **`Playwright_Hero_DMS_fill`** accepts **`customer_id`** / **`vehicle_id`**; **`_persist_dms_scrape_to_db`** calls **`update_vehicle_master_from_dms`** + **`update_sales_master_from_dms_scrape`** after **Add Enquiry** success, **stage 5** vehicle grid scrape, and **video** **`create_order`** merge (end-of-run persist retained) |
| 6.27 | Mar 2026 | — | **`_add_customer_payment`**: **`_payment_lines_has_existing_row`** — if Payment Lines jqgrid/list already has a data row, skip **`+`** and row fill, return success (continue to Vehicle Sales); else **`+`** path fills **Transaction Amount** **120000** (was placeholder **0**) |
| 6.28 | Mar 2026 | — | **Add Sales** client: buttons **Create Invoice** / **Generate Insurance** (labels); **Create Invoice** / **Generate Insurance** / **Print Forms** gated on **`submitInfoActionsComplete`** (`hasSubmittedInfo` plus non-null **`lastSubmittedCustomerId`** / **`lastSubmittedVehicleId`** from Submit Info) and disabled while **`isSubmitting`**; eligibility query unchanged (`/add-sales/create-invoice-eligibility`) |
| 6.29 | Mar 2026 | — | **`customer_master.dms_contact_id`** (`DDL/alter/02k_customer_master_add_dms_contact_id.sql`); **Print Forms and Queue RTO** enables only when **New**, **Submit Info**, **Create Invoice**, and **Generate Insurance** are all disabled (and eligibility not loading); **New** locked after Submit until first Print or loading; **Submit Info** disabled after successful submit until **New**; **`hasPrintedForms`** allows repeat Print after **New** re-enables |
| 6.30 | Mar 2026 | — | **§2.2a** Add Sales **staging**: Postgres **`add_sales_staging`** + UUID **`staging_id`** (no Redis v1); planned **`POST`/`PUT /add-sales/staging`**, **`staging_id`** on **`/fill-dms`**, optional **`staging_id`** on eligibility; script **`DDL/alter/13a_add_sales_staging.sql`** |
| 6.31 | Mar 2026 | — | Dropped **`form_dms_view`**; DMS fill via **`form_dms.py`** inline SQL + target **`add_sales_staging.payload_json`** (**`DDL/alter/13b_drop_form_dms_view.sql`**) |
| 6.32 | Mar 2026 | — | **`staging_id`** on **`/fill-dms`**, **`/fill-dms/dms`**: **`fetch_staging_payload`** (draft/committed), **`build_dms_fill_row_from_staging_payload`** — no master reads for fill; legacy IDs optional |
| 6.33 | Mar 2026 | — | **`POST /submit-info`**: **`persist_staging_for_submit`** — draft **`add_sales_staging`** row in the same transaction as master upserts; response **`staging_id`**; optional request **`staging_id`** updates same-dealer draft; Add Sales client persists **`lastStagingId`** and sends **`staging_id`** on Create Invoice |
| 6.34 | Mar 2026 | — | ~~**`form_insurance_view` cold start:** minimal **`insurance_master` seed**~~ — superseded by **6.35** |
| 6.35 | Mar 2026 | — | **BR-20**: **`build_insurance_fill_values`** merges **`add_sales_staging.payload_json`** when **`staging_id`** is set; **`fetch_staging_payload`** (draft/committed); **`/fill-dms/insurance`**, **`/insurance/hero`** request fields |
| 6.36 | Mar 2026 | — | **BR-20**: **§2.2a** rollout + API table — **`staging_id`** on **Generate Insurance**; view + **`payload_json`** documented as joint complete input set |
| 6.37 | Mar 2026 | — | **FR-18b**: **`insert_insurance_master_after_gi`** (INSERT only; duplicate triple **ValueError**); **`click_issue_policy_and_scrape_preview`** + **`update_insurance_master_policy_after_issue`** |
| 6.38 | Mar 2026 | — | Removed **`dummy-sites/`** and static **`/dummy-*`** mounts; **`DMS_MODE`** default **`real`**; **`dummy`** rejected at startup; Siebel-only Fill DMS; Vaahan/RTO Pay stubs until production automation |
| 6.39 | Mar 2026 | — | **`prepare_vehicle`**: pre-booking vehicle prep (Auto Vehicle List search/scrape, **`_siebel_fill_key_battery_from_dms_values`**, In Transit receipt + Pre Check/PDI); **`Playwright_Hero_DMS_fill`** stage 5 delegates to it; Add Enquiry key/battery fill uses the same helper |
| 6.40 | Mar 2026 | — | **`_merge_dms_and_grid_for_vehicle_master`** + **`_vehicle_master_prepare_gaps`** after **`prepare_vehicle`**; merged keys appended to **`Playwright_DMS.txt`** under **`--- vehicle_master ---`**; **`place_of_registeration`** / **`oem_name`** noted as persist-time from **`dealer_ref`** |
| 6.41 | Mar 2026 | — | Video path hardening: run **`prepare_vehicle`** before Contact Find; Add Enquiry now hard-fails when Enquiry# is empty; payments require a post-save Payment Lines row; Generate Booking stage fails run when the control is missing (booking mandatory) |
| 6.42 | Mar 2026 | — | **Video path booking strict-fail** before `create_order` when Generate Booking is unavailable; added immediate `Playwright_DMS.txt` **vehicle_snapshot** blocks after each scrape/merge update (`prepare_vehicle`, Add Enquiry save, Contact_Enquiry hit, create_order merge, linear stage 5 merge) |
| 6.43 | Mar 2026 | — | ~~Temporary gates **`SIEBEL_DMS_FORCE_FAIL_BEFORE_FIND_CONTACT`** / **`SIEBEL_DMS_FORCE_FAIL_BEFORE_FILL_RELATIONS_NAME`**~~ **removed** (diagnostics-only list notes remain on video path) |
| 6.44 | Mar 2026 | — | **`prepare_vehicle`**: after grid + key/battery, scrape vehicle applet by aria-label (**VIN**, **Model**, **Manufacturing Year**, **SKU**/label SKU, **Color**, **Engine Number**); **Serial Number** drilldown → **Features and Image** tab → **`4_s_1_l_HHML_Feature_Value`** (and `5_s_1_l_*` / next row) for cubic + vehicle type; **merge** prefers detail **`full_chassis`**/**`full_engine`** before grid; tab PreCheck/PDI on that view **not** duplicated (remains in **`_attach_vehicle_to_bkg`**). *At the time: In Transit URL Pre-check was separate; URL implementation **removed** — **6.49**.* |
| 6.45 | Mar 2026 | — | **`prepare_vehicle`**: **`vehicle_in_transit`** from **`aria-label="Inventory Location"`** — substring **in transit** → hard fail **`Vehicle is in transit. Create Receiving before Booking.`**; substring **dealer** → **`in_transit=False`**; other non-empty → **`in_transit=False`** (overrides grid); empty → keep list-grid heuristic |
| 6.46 | Mar 2026 | — | **`_siebel_run_vehicle_serial_detail_precheck_pdi`**: shared tab Pre-check + PDI (+ feature-id cubic/type scrape) after **Serial Number** drilldown; **`prepare_vehicle`** and **`_attach_vehicle_to_bkg`** both call it; attach doc/steps use single-click **VIN** drilldown (no separate double-click step for VIN) |
| 6.47 | Mar 2026 | — | **`prepare_vehicle`** step order: grid scrape → left-pane VIN drill-in → Key/Battery → aria detail → **Inventory Location** gate → **Serial Number** tab Pre-check/PDI + Features when **dealer** stock (see **6.48** for in-transit / URL policy) |
| 6.48 | Mar 2026 | — | **`prepare_vehicle`**: single Pre-check/PDI path — tab **`_siebel_run_vehicle_serial_detail_precheck_pdi`** **only** when `in_transit` is false after inventory gate; **in-transit** → receipt URL / **Process Receipt** only (Siebel rejects Pre-check/PDI until dealer stock) |
| 6.49 | Mar 2026 | — | Removed unused URL Pre-check/PDI implementation: **`_siebel_run_precheck_and_pdi`**, **`_try_click_precheck_complete`**, **`_try_click_pdi_submit`**. Env **`DMS_REAL_URL_PRECHECK`** / **`DMS_REAL_URL_PDI`** remain in **`config.py`** / **`SiebelDmsUrls`** for optional future or operator use |
| 6.50 | Mar 2026 | — | **`_siebel_goto_vehicle_list_and_scrape`**: when **frame_partial** + **engine_partial** are set, uses same **Find → Vehicles** path as Add Enquiry (**`_siebel_prepare_vehicle_list_find_vin_engine`**): **`_try_prepare_find_vehicles_applet`** (incl. **`aria-label="Find ComboBox"`**), **`#findfieldsbox`** or **`#findfieldbox`**, *VIN/*Engine + **Find**; legacy key/chassis inputs only as fallback |
| 6.51 | Mar 2026 | — | **`_siebel_goto_vehicle_list_and_scrape`**: **only** Find→Vehicles **`*`**VIN/**`*`**Engine partials (**no** legacy key/grid field fallback); hard error if either partial empty or fill fails. **`_create_order`**: removed **Vehicle Sales** pre-**+** Find → **Mobile Phone#** existing-order branch (**`_try_vehicle_sales_find_existing_order`**); superseded **LLD** **6.20** / **6.22** for that behavior |
| 6.52 | Mar 2026 | — | **`_siebel_run_vehicle_serial_detail_precheck_pdi`**: Pre-check and PDI tab clicks now prioritize the **Third Level View Bar** container (then fallback selectors/ids). **`_merge_dms_and_grid_for_vehicle_master`** now enforces **`year_of_mfg`** normalization to strict **`YYYY`** before persist/log merges |
| 6.53 | Mar 2026 | — | **`customer_address_infer.normalize_address_freeform`**: strips stray OCR 6-digit PIN token immediately before Indian state name (e.g. before **Rajasthan**) so address lines keep only the correct trailing state/PIN sequence |
| 6.54 | Mar 2026 | — | **`_siebel_run_vehicle_serial_detail_precheck_pdi`**: Third Level View Bar tab match is **hyphen-insensitive** (**Pre-check** vs **PreCheck**); **`Page` / `main_frame` first**, **`FrameLocator` roots skipped** for `evaluate`; fixed tab ids were rejected as unstable across runs (e.g. `ui-id-160/158` then `ui-id-192/190`), so text-first matching remains authoritative; legacy Pre-check id fallback **`ui-id-1115`** kept as last resort |
| 6.55 | Mar 2026 | — | Third Level View Bar: tab activation iterates **`a` / `button` / `[role='tab']` only** — not **`li`/`span`** wrappers — to match jQuery UI (clicking **`li`** matched label but did not switch tab; debug NDJSON could show `ok: true` spuriously) |
| 6.56 | Mar 2026 | — | **`#s_vctrl_div`** (Siebel view-control header) is tried **first** for PreCheck/PDI tab anchors, then aria “Third Level View Bar” nodes; click path adds **`focus()`** + synthetic **mouse** events after native **`click()`** for stubborn Open UI tabs |
| 6.57 | Mar 2026 | — | Third Level tab hardening: if match lands on **`LI`** (e.g. `role=tab` on wrapper), resolve and click inner **`a` / `button` / `[role='tab']`** target; NDJSON now logs both **`matchedTag`** and actual **`clickedTag`** / **`clickId`** |
| 6.58 | Mar 2026 | — | **Pre-check** tab: after tab switch, Technician pick icon tries **`s_3_2_25_0_icon`** first, then legacy **`s_3_1_12_0_Ctrl`**; NDJSON **`precheck_technician_icon_click`** records **`used_id`** |
| 6.59 | Mar 2026 | — | **`_siebel_run_vehicle_serial_detail_precheck_pdi`**: after **PDI** tab, probes list rows and **PDI Expiry** column; if rows exist and latest parsed expiry is **on or after today**, skips **Service Request List:New**, pick icon, and **Submit** (logs valid PDI; **`form_trace`** **`pdi_valid_existing_skipped_new_row`**). If **no rows** or latest parsed expiry is **before today**, runs the existing new-row + pick + Submit path. NDJSON **`pdi_existing_probe`** (**G6**) records counts and parsed max expiry |
| 6.60 | Mar 2026 | — | **`_siebel_fill_key_battery_from_dms_values`**: vehicle form fill order is **Battery No.** then **Key Number**; frame detection prefers a frame that exposes **Battery No.** (then **Key Number**) |
| 6.61 | Mar 2026 | — | Vehicle scrape: **`horse_power`** removed; grid/merge use **`vehicle_price`** only (ex-showroom persisted as **`vehicle_ex_showroom_price`**); **`cubic_capacity`** normalized to numeric cc digits; after PDI, **Features** tab + second HHML scrape skipped when **`cubic_capacity`** and **`vehicle_type`** are already set; **`vehicle_master.horse_power`** dropped (**`15b`**) |
| 6.62 | Mar 2026 | — | **`_attach_vehicle_to_bkg`**: no DOM scrapes (no **Total**/`vehicle_ex_showroom_cost`, no feature-id cubic/type, no Invoice#); **`_siebel_run_vehicle_serial_detail_precheck_pdi`** with **`do_feature_id_scrape=False`**; returns empty **`{}`**; **Invoice#** / **Order#** remain from **`_create_order`** post-attach scrapes; legacy **Invoice Selected** path still scrapes Total/inventory where applicable |
| 6.63 | Mar 2026 | — | **`_merge_dms_and_grid_for_vehicle_master`**: **`_best_chassis_str`** / **`_best_engine_str`** prefer full VIN / full engine over DMS partials; **`frame_num`**/**`engine_num`** dropped after merge; bogus grid **`vehicle_price`** (non-numeric descriptions) and invalid seating/cylinder cells stripped; **`_apply_two_wheeler_seating_cylinders_body`** on merge; **`Playwright_DMS.txt`**: single **`vehicle_master`** block (no duplicate snapshot after **`prepare_vehicle`**); log keys omit **`frame_num`**/**`engine_num`** |
| 6.64 | Mar 2026 | — | **`_attach_vehicle_to_bkg`**: Siebel **Create Invoice** click gated by **`_ATTACH_VEHICLE_AUTO_CLICK_CREATE_INVOICE`** (default **False** — operator completes invoice in UI) |
| 6.65 | Mar 2026 | — | **`_find_contact_mobile_first_grid_counts`**: video path **NOTE** snapshot uses **mobile + drilldown** (same basis as **`_contact_find_title_sweep_for_enquiry`** ordinals), not strict first name on row text; optional strict first-name drillable count logged |
| 6.66 | Mar 2026 | — | **`_contact_enquiry_tab_has_rows`**: when **`1_HHML_Enquiry_Status`** / **`*HHML_Enquiry_Status`** fields exist in the DOM, enquiry row counts and numbers use only rows whose status normalizes to **Open**; if no such fields exist, prior detection behavior is unchanged |
| 6.67 | Mar 2026 | — | **`Playwright_Hero_DMS_fill` video path**: branch **A** when **N=0** drilldown rows → **`_add_enquiry_opportunity`** + re-find; else **`_contact_find_title_sweep_for_enquiry`** unchanged; **`contacts_with_open_enquiry`** ∈ {0,1}; branch **(2)** re-find + drill ordinal 0 → **`_siebel_video_branch2_address_postal_and_save`** (`#s_vctrl_div`, `#1_Postal_Code`); **`_write_playwright_contact_scrape_section`**; dotted-first recovery loop removed |
