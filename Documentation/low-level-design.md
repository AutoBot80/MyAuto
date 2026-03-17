# Low Level Design (LLD)
## Auto Dealer Management System

**Version:** 0.2  
**Last Updated:** March 2025

---

## 1. Client (React) Structure (Modular)

- **Layout:** `AppLayout` composes `Header` + `Sidebar` + main content slot.
- **Pages:** `AddSalesPage`, `AiReaderQueuePage`, `RtoPaymentsPendingPage`, `ViewCustomerPage`, `PlaceholderPage`.
- **API:** `api/client.ts` (base URL, `apiFetch`), `api/uploads.ts`, `api/aiReaderQueue.ts`, `api/fillDms.ts`, `api/submitInfo.ts`, `api/rtoPaymentDetails.ts`, `api/customerSearch.ts` — microservice-friendly; swap base URL per env.
- **Hooks:** `useToday`, `useUploadScans`, `useAiReaderQueue` — reusable, testable.
- **Types:** `types/index.ts` — `Page`, `AddSalesStep`, `AiReaderQueueItem`, `ExtractedVehicleDetails`, `PrintForm20Response`, etc.

### 1.1 Key Components

| Component | Purpose |
|-----------|---------|
| `App` | Root; composes `AppLayout` and page by route. |
| `AppLayout` | Header + Sidebar + main slot. |
| `Header` | Dealer name (centre), right slot (e.g. date). |
| `Sidebar` | Nav links; `onNavigate(page)`. |
| `AddSalesPage` | Add Sales flow: Submit Info, Fill Forms (DMS, Vahan, Form 20), Insurance tiles. |
| `UploadScansPanel` | Step tiles, file input, upload button, uploaded list. |
| `AiReaderQueuePage` | Uses `useAiReaderQueue`; renders `AiReaderQueueTable`. |
| `RtoPaymentsPendingPage` | List RTO applications; record payment. |
| `ViewCustomerPage` | Search by mobile/plate; view vehicles and insurance. |
| `PlaceholderPage` | Title + message for coming-soon pages. |

---

## 2. Backend (FastAPI) Structure (Modular / Microservice-Friendly)

### 2.1 Module Layout

```
backend/app/
  main.py              # App factory, CORS, include_router
  config.py            # DATABASE_URL, UPLOADS_DIR, APP_ROOT, FORM20_*, etc.
  db.py                # get_connection()
  schemas/             # Pydantic request/response (uploads, ocr, fill_dms, etc.)
  repositories/        # Data access only (ai_reader_queue, dealer_ref, rto_payment_details, rc_status_sms_queue)
  services/            # Business logic (UploadService, form20_service, fill_dms_service, submit_info_service, rto_payment_service)
  routers/             # health, uploads, ai_reader_queue, fill_dms, submit_info, rto_payment_details, customer_search, dealers, documents, qr_decode, vision, textract_router
  templates/           # form20_front.html, form20_back.html, form20_page3.html
```

- **Routers:** Thin; call services or repos. Each router can be mounted or split into a separate service later.
- **Services:** Stateless, injectable (e.g. `UploadService(uploads_dir=...)`).
- **Repositories:** Table access only; no business rules.

### 2.2 API Endpoints (Current)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness. |
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
| POST | `/fill-dms` | Full Fill DMS flow (DMS + Vahan). |
| POST | `/fill-dms/dms` | DMS only. |
| GET | `/fill-dms/data-from-dms` | Get data from DMS.txt. |
| GET | `/fill-dms/form20-status` | Form 20 template status. |
| POST | `/fill-dms/print-form20` | Generate Form 20.pdf. |
| POST | `/fill-dms/vahan` | Vahan (RTO) only. |
| GET | `/rto-payment-details` | List RTO applications. |
| GET | `/rto-payment-details/by-sale` | Get RTO by sale (customer_id, vehicle_id). |
| POST | `/rto-payment-details` | Create RTO application. |
| POST | `/rto-payment-details/{application_id}/pay` | Record payment. |
| GET | `/customer-search/search` | Search by mobile or plate. |
| GET | `/dealers/{dealer_id}` | Get dealer by ID. |
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

### 2.4 Form 20 Generation

- **Service:** `form20_service.py` — `generate_form20_pdfs(subfolder, customer, vehicle, vehicle_id, dealer_id)`.
- **Flow:** Prefer Word template (`templates/word/FORM 20 Template.docx`) → fill placeholders → convert to PDF (docx2pdf or LibreOffice) → output `Form 20.pdf` (all pages). Fallback: PDF overlay on Official FORM-20 or separate templates. Fallback: HTML templates. Override via env `FORM20_TEMPLATE_DOCX`.
- **Gate Pass:** Word template (`templates/word/Gate Pass Template.docx`) → fill placeholders → convert to PDF → output `Gate Pass.pdf`. Placeholders: `{{field_0_today_date}}`, `{{field_1_oem_name}}`, `{{field_2_customer_name}}`, `{{field_3_aadhar_id}}`, `{{field_4_model}}`, `{{field_5_color}}`, `{{field_6_key_num}}`, `{{field_7_chassis_num}}`. Override via env `GATE_PASS_TEMPLATE_DOCX`.
- **Placeholders:** `{{field_0_city}}`, `{{field_1_name}}`, `{{field_2_care_of}}`, `{{field_3_address}}`, `{{field_10_dealer_name}}`, `{{field_14_body_type}}`, `{{field_16_oem_name}}`, `{{field_17_year_of_mfg}}`, `{{field_20_cubic_capacity}}`, `{{field_21_model}}`, `{{field_22_chassis_no}}`, etc.

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
| `rto_payment_details` | RTO applications; application_id PK; sales_id FK. |
| `rc_status_sms_queue` | RC status SMS queue; sales_id FK. |

---

## 4. Queue and Workers (Planned)

### 4.1 Queues

- **ocr_queue:** Message = { document_id, s3_key, options }.
- **automation_queue:** Message = { job_id, target_system, entity_refs }.

### 4.2 OCR Worker

- Poll or subscribe to ocr_queue; download file from S3; run Tesseract; parse and store result in DB; update `ai_reader_queue` status.

### 4.3 Playwright Worker

- Poll or subscribe to automation_queue; load job; fetch related rows from DB; start browser; run site-specific script (login, navigate, fill, submit); update status and store artifacts.

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
