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
  services/            # Business logic (UploadService, bulk_job_service, bulk_queue_service, bulk_watcher_service, form20_service, fill_dms_service, siebel_dms_playwright, submit_info_service, rto_payment_service)
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
| GET | `/settings/site-urls` | Returns `dms_base_url`, `dms_mode`, `dms_real_siebel`, `dms_real_contact_url_configured`, `vahan_base_url`, `insurance_base_url` from `backend/.env` (required at server startup; used by the client for Fill DMS and messaging; no in-code URL fallbacks). |
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
| POST | `/submit-info` | Upsert customer, vehicle, sales, insurance. |
| GET | `/bulk-loads` | List bulk dashboard rows from hot `bulk_loads` only. |
| GET | `/bulk-loads/counts` | Bulk tab counts from hot data only; `Error` and `Rejected` exclude `action_taken=true`. |
| GET | `/bulk-loads/pending-count` | Count unresolved `Error` + `Rejected` hot rows for the nav badge. |
| PATCH | `/bulk-loads/{bulk_load_id}/action-taken` | Mark an `Error` or `Rejected` row corrected. |
| POST | `/bulk-loads/{bulk_load_id}/prepare-reprocess` | Copy error artifacts back to uploads and start OCR for manual retry. |
| PATCH | `/bulk-loads/{bulk_load_id}/mark-success` | Mark a manually completed error as success. |
| GET | `/bulk-loads/folder/{folder_path}/list` | List files in a bulk result folder. |
| GET | `/bulk-loads/file/{file_path}` | Download or preview a file from a bulk result folder. |
| POST | `/fill-dms` | Full Fill DMS flow; reuses already open logged-in DMS/Vahan tabs when detectable, otherwise auto-opens Edge/Chrome and returns first-time-login guidance. Response includes `dms_milestones` (checklist labels) and, in real Siebel mode, `dms_step_messages` (ordered operator-facing sentences — Add Sales banner prefers these when non-empty). |
| POST | `/fill-dms/dms` | DMS only; `DMS_MODE=dummy` runs static HTML under `DMS_BASE_URL`; `DMS_MODE=real` runs `siebel_dms_playwright.run_hero_siebel_dms_flow` (**BRD §6.1a** — see **§2.4d**). **`skip_find`**: dummy may skip contact finder Go; **real Siebel always** runs Contact Find first (`DMS_REAL_URL_CONTACT`), then linear SOP ( **`skip_find` in DB is ignored**). **Generate Booking** after vehicle for all paths; allotment when **not** In Transit. Env: `DMS_SIEBEL_*`, `DMS_REAL_URL_*`. Same response fields as `/fill-dms` for DMS: `dms_milestones`, `dms_step_messages` (real Siebel). |
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
- **Aadhaar (Add Sales / queue):** `OcrService._process_aadhar` and upload-time `_pipeline_merge_aadhar_customer` use **AWS Textract** on **`Aadhar.jpg`** (and on **`Aadhar_back.jpg`** when geo fields are still weak). Parsed customer fields merge into **existing** JSON `customer` via `_merge_qr_customer_into_existing` (name is historical; it fills blanks from the Aadhaar fragment). **No UIDAI QR decode** runs in scans-v2 or `get_extracted_details`. Ad-hoc QR decode remains on **POST `/qr-decode`** (`qr_decode_service`). **`get_extracted_details`** applies **Textract fallback (Raw_OCR):** after `Raw_OCR.txt` is built (`--- Aadhar.jpg ---` / `--- Aadhar_back.jpg ---` sections), `OcrService` parses that text — **DOB** from labeled lines, **`/DB:`** / **`DB:`** (mis-OCR of DOB) before a slash date, and a marker-proximity pass that prefers **`dd/mm/yyyy`** near DOB/DB tokens (slash density in the local window); (avoiding **issued** dates when possible); **gender** via **DOB anchor** (after `dd/mm/yyyy`, skip one token, next `/`, gender token) plus **`Gender:`** / **Sex/** / **yes/** fallbacks; **address** from back (including `Address` + newline + `C/O:` / **`S/O:`** + `Near …` without stopping the `Address:` block at relation lines). `customer_address_infer.normalize_address_freeform` sets **`care_of`** as **`C/o`/`S/o`/`W/o`/`D/o` + name**, strips that clause from the body, **prepends** it to the composed **`address`**, and uses **`DIST: district, state - PIN`**, **comma-separated clauses** with a **trailing dash run** before the last 6-digit **PIN**, and trailing **`<state> - <PIN>`**; known Indian states/UTs for **state** / **PIN**. Applied at end of `process_uploaded_subfolder`, again in `get_extracted_details` when `Raw_OCR.txt` exists. If **gender** is still empty after those steps (and Aadhaar uploads exist), `OcrService` sets **`customer.gender` = `Male`** (`_default_gender_male_if_unread`). Constructed `address` uses POA-style parts when present; when merging into an existing customer, a non-empty Details `address` is **not** overwritten by a shorter composed line.
- **Sales detail template mapping:** Details-sheet extraction also supports the A5 Sales Detail Sheet label style (e.g., `Full Name`, `Mobile Number`, `Aadhaar Number`, `Profession`, `Marital Status`, `Nominee ...`, `Financier Name`) and merges these into `OCR_To_be_Used.json` (`customer` + `insurance`) for Add Sales auto-population.
- **Add Sales v2 `Details.jpg`:** The file is always stored under that name; content is detected by magic bytes (JPEG/PNG/PDF or ZIP-based `.docx`), so Word exports mislabeled as `.jpg` still use the docx parser instead of Textract on invalid bytes.
- **Vehicle fields from Details:** Textract FORMS keys are mapped with normalized labels (including `Chassis Number`, `Engine Number`, etc.). If pairs are sparse on a PDF, `full_text` is parsed for the same labels (e.g. `Chassis Number: … Engine Number: …` on one line).

### 2.4 Form 20 Generation

- **Service:** `form20_service.py` — `generate_form20_pdfs(subfolder, customer, vehicle, vehicle_id, dealer_id)`.
- **Flow:** Prefer Word template (`templates/word/FORM 20 Template.docx`) → fill placeholders → convert to PDF (docx2pdf or LibreOffice) → output `Form 20.pdf` (all pages). Fallback: PDF overlay on Official FORM-20 or separate templates. Fallback: HTML templates. Override via env `FORM20_TEMPLATE_DOCX`.
- **Gate Pass:** Word template (`templates/word/Gate Pass Template.docx`) → fill placeholders → convert to PDF → output `Gate Pass.pdf`. Placeholders: `{{field_0_today_date}}`, `{{field_1_oem_name}}`, `{{field_2_customer_name}}`, `{{field_3_aadhar_id}}`, `{{field_4_model}}`, `{{field_5_color}}`, `{{field_6_key_num}}`, `{{field_7_chassis_num}}`. Override via env `GATE_PASS_TEMPLATE_DOCX`.
- **Placeholders:** `{{field_0_city}}`, `{{field_1_name}}`, `{{field_2_care_of}}`, `{{field_3_address}}`, `{{field_10_dealer_name}}`, `{{field_14_body_type}}`, `{{field_16_oem_name}}`, `{{field_17_year_of_mfg}}`, `{{field_20_cubic_capacity}}`, `{{field_21_model}}`, `{{field_22_chassis_no}}`, etc.

### 2.4a Dummy Vahan Flow

- **Static site:** `dummy-sites/vaahan/` simulates the real VAHAN navigation used by Playwright tests.
- **Pages:** landing/start (`index.html`) → owner/details entry (`application.html`) → assigned office/worklist (`search.html`) → payment gateway (`payment.html`) → bank login (`bank-login.html`) → bank confirmation (`bank-confirm.html`).
- **Automation contract:** `fill_dms_service.py` reads DMS field values only from `form_dms_view`, writes `ocr_output/<dealer>/<subfolder>/DMS_Form_Values.txt`, and updates `vehicle_master` with the DMS scrape. The **Order Value / ex-showroom** amount from the vehicle grid is stored in `vehicle_master.vehicle_price` (same column; UI label is ex-showroom). DMS/Vahan automation first attempts to reuse already open logged-in tabs; if no matching detectable tab is available, backend auto-opens Edge/Chrome to the target site and returns an operator message to login first-time and retry. The Add Sales page stops on that message and avoids downstream processing, then resumes normally on retry.

### 2.4b Dummy DMS Flow

- **Static site:** `dummy-sites/dms/` simulates **Hero Connect / Oracle Siebel eDealer** (tabs and sub-tabs aligned to the DMS Process Video). Shared chrome: `dms-layout.css` (Siebel header, **Find** bar, main module tabs, sub-tabs, inner tab rows).
- **Pages:** Login (`index.html` → `enquiry.html`) → **Enquiry / My Enquiries** (`enquiry.html`) → **Vehicle Sales / My Vehicle Sales** (`my-sales.html`) → **Invoice / Allotment** (`line-items.html`) → **Auto Vehicle List** (`vehicle.html`) → **Vehicles** record view (`vehicles.html`) → **Contacts / Payments** (`contacts-payments.html`) → **PDI** (`pdi.html`) → **Run Report** + downloads (`reports.html`) → optional **invoice** (`invoice.html`).
- **Address → State / PIN / Care of:** `customer_address_infer` parses **`C/O:`**, **`S/o:`**, **`W/o:`**, **`D/o:`** into **`care_of`** (canonical **`C/o`/`S/o`/`W/o`/`D/o` + name**) and **`DIST: <District>, <State> - <PIN>`** into **city/district**, **state**, and **PIN**; strips the relation clause from the body, **prepends** **`care_of`** to **Address Line 1** when building **`address`**; **truncates after the last 6-digit PIN** (junk after PIN ignored). `normalize_address_freeform` implements the parse; **`enrich_customer_address_from_freeform`** merges into customer JSON / Submit Info. **`fill_dms_service._build_dms_fill_values`** uses the same enrichment for **Address Line 1**, **State**, **Pin Code**, and **Father or Husband** when the DB row is sparse.
- **Automation contract:** `fill_dms_service.py` requires `customer_id` and `vehicle_id`, loads DMS field values from `form_dms_view`, and drives the dummy DMS in order: **Enquiry** (`"DMS Contact Path"`: `found`, `new_enquiry`, or **`skip_find`** — dummy: skip finder Go, then form + **Generate booking**), S/O or W/o + father/husband, customer budget **89000**, generate booking → **Vehicles** → **PDI** → **Auto Vehicle List** → **Enquiry** (allocate) → **Invoice line** (no Create Invoice) → **Reports**. **`DMS_MODE=real`:** `siebel_dms_playwright.run_hero_siebel_dms_flow` follows **BRD §6.1a** (see **§2.4d**); **`skip_find` in DB does not bypass Contact Find** — always Find→Contact, mobile **only**, Go first. **Linear SOP** after find: **match** (table row, not Find field only) → skip basic enquiry; no match or `new_enquiry` → **basic enquiry** (name/address/state/PIN **only**) + Save → **mandatory re-find** by mobile → **care-of** + Save (care-of **always**); then vehicle; **Generate Booking** **always** after vehicle (in-transit or not); allotment (line items / Price All / Allocate) **only** when **not** In Transit; invoice step = operator message only. **`dms_siebel_forms_filled`**: Save detected on customer steps **and** vehicle list OK. **`routers/fill_dms.py`:** *No such vehicle found in DMS* unless `dms_siebel_forms_filled` false in real mode.
- **Order note:** The dummy sequence **does not** match **BRD §6.1a** exactly (e.g. it always runs **Generate booking** before vehicle search and does not branch on Siebel **In Transit**). See **§2.4d** for a parity table.

### 2.4c Dummy Insurance Flow

- **Static site:** `dummy-sites/insurance/` simulates the insurance issuance journey from the operator video.
- **Pages:** login redirection (`index.html`) -> KYC verification (`kyc.html`) -> KYC success redirect (`kyc-success.html`) -> MisDMS VIN entry (`dms-entry.html`, VIN/Frame = chassis from DMS) -> New Policy (`policy.html`, Ex-Showroom = DMS cost / `vehicle_price`, `#ins-issue-policy` for manual issue only) -> issue-result (`issued.html`).
- **Serve path:** `main.py` mounts this directory at `/dummy-insurance`.
- **Video-label parity:** top-level labels mirror observed strings (`Hero INSURANCE BROKING`, `HIBIPL - MisDMS Entry`, `New Policy - Two Wheeler`), including key menu items and KYC controls.
- **Automation contract:** Insurance Playwright uses persisted DB values (`customer_master`, `vehicle_master`, `insurance_master`, `dealer_ref` / `oem_ref`). **Insurer** for `#ins-company` / `#ins-sel-policy-company` is fuzzy-matched from **`insurance_master.insurer`**, or if empty from **`OCR_To_be_Used.json`** `insurance.insurer` (Details sheet text such as `Insurer Name (if needed): SOMPO` → **Universal Sompo General Insurance** on the dummy portal). **Open login first** (`require_login_on_open=false`): managed browser loads the insurance base URL (dummy `index.html` = MISP-style login), then waits up to **`INSURANCE_LOGIN_WAIT_MS`** for the operator to sign in and for the **KYC** screen (dummy `kyc.html` or URL hints `ekycpage` / `kycpage.aspx` / `/ekyc`). Then: **Insurance company** fuzzy-match, **fill mobile** → **Verify mobile** → if `need_docs`, three uploads + consent + **Submit** (`#ins-kyc-submit`); if KYC found, **Proceed** only; then kyc-success → DMS entry → policy details. **Manufacturer** fuzzy-match to `vehicle_master.oem_name` / `oem_ref`. Does not click Issue Policy; writes `Insurance_Form_Values.txt`.

### 2.4d Real Siebel DMS — BRD §6.1a checklist vs Playwright

**Source of truth for intended steps:** `Documentation/business-requirements-document.md` **§6.1a**. **Code:** `backend/app/services/siebel_dms_playwright.py` (`run_hero_siebel_dms_flow`, nested `stage_5_vehicle_flow` for vehicle + In-Transit), `backend/app/services/fill_dms_service.py` (`_run_fill_dms_real_siebel_playwright`, `run_fill_dms_only`). **Operator trace:** each real **`/fill-dms/dms`** run overwrites `ocr_output/<dealer_id>/<subfolder>/Playwright_DMS.txt` with a live UTC log (values used, STEP/NOTE/MILESTONE, **`[FORM]`** lines: `siebel_step`, form/screen label, action, field=value pairs, DECISIONs, `[END]` + error). Template folder `ocr_output/dealer/mobile_ddmmyyyy/` only explains this (no static SOP copy).

| BRD §6.1a step | Intended Siebel action | Dummy (`DMS_MODE=dummy`) | Real Siebel (`DMS_MODE=real`) |
|----------------|------------------------|---------------------------|-------------------------------|
| 0 | Logged-in session | Training `index.html` → `enquiry.html` | Operator/CDP; `_get_or_open_site_page` (no scripted Hero login) |
| 1 | Find contact by mobile | Yes unless `skip_find` | **Always** Find→Contact, **mobile only**, **Go** (`skip_find` in DB does not bypass) |
| 2a | New customer: basic enquiry + re-find + care | `new_enquiry` path | **`new_enquiry`** or **no UI match** → `_fill_basic_enquiry_details` + **Save**; mandatory **`_refind_customer_after_enquiry`**; then **`_fill_siebel_care_of_only`** + **Save**; milestone **Enquiry created** |
| 2b | Existing: skip basic + care always | **Gap:** always full enquiry on dummy | **Heuristic:** `_siebel_ui_suggests_contact_match` (table row, ≥3 **`td`**) → skip stages 2–3; **always** stage 4 care-of + **Save**; milestones **Customer found**, **Care of filled** |
| 3 | Vehicle search; **In Transit** vs other | Dummy order differs; no status logic | **`scrape_siebel_vehicle_row`** sets **`in_transit`** if grid text matches `in transit` |
| 4a | Receipt, **Pre Check**, PDI | `vehicles.html` + precheck + `pdi.html` | If **`in_transit`:** receipt URL → **Process Receipt**; **`_siebel_run_precheck_and_pdi`**: optional **`DMS_REAL_URL_PRECHECK`**, else Pre Check click on **`DMS_REAL_URL_PDI`** before **PDI Submit** (no generic **Submit** — PDI-specific labels only); milestone **Pre check completed** |
| 4b | Booking + allocate | Generate booking before vehicle (dummy) | **Generate Booking** **after** vehicle for **both** branches; if **not** `in_transit`: then `goto` **`DMS_REAL_URL_LINE_ITEMS`**, **Price All** (optional), **Allocate** / **Allocate All** |
| `skip_find` | Enquiry without Find | Skips finder | **Ignored** for real automation: always **Find→Contact** first (even if DB says `skip_find`), then linear SOP; **Generate Booking** after vehicle (**always**); allotment when **not** In Transit |
| BR-16 | No **Create Invoice** | Compliant | Compliant; `_requires_operator_create_invoice` may still block if UI demands operator |
| Milestones | — | Still uses **Invoice created** on dummy line view | **Booking generated**, **Allotment view opened**, **Vehicle allocated** (not “Invoice created”) |
| — | Browser left open | `_KEEP_OPEN_BROWSERS` / CDP | Same |

**Residual gaps / tuning:** Contact match and **In Transit** are **heuristic** (tenant grid/layout may need selector or copy tweaks). **Process Receipt**, **PDI Submit**, and **Allocate** use toolbar name patterns; dialogs/OTP/exchange/finance are **not** automated. **Reports** URL is not auto-opened. Dummy flow remains **linear** and intentionally **not** reordered to §6.1a.

### 2.5 Database Access

- **Connection:** `get_connection()` in `db.py` using `DATABASE_URL`.
- **Insurance Playwright tuning (optional `.env`):** `INSURANCE_ACTION_TIMEOUT_MS` (default 5500) for KYC/navigation actions; `INSURANCE_POLICY_FILL_TIMEOUT_MS` (default 3200) while filling the policy / insurance-details form. Lower values speed up local dummy runs; raise if a slow portal flakes.
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
