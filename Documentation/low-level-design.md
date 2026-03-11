# Low Level Design (LLD)
## Auto Dealer Management System

**Version:** 0.1  
**Last Updated:** March 2025

---

## 1. Client (React) Structure (Modular)

- **Layout:** `AppLayout` composes `Header` + `Sidebar` + main content slot.
- **Pages:** `AddSalesPage`, `AiReaderQueuePage`, `PlaceholderPage` (Customer Details, RTO Queue).
- **API:** `api/client.ts` (base URL, `apiFetch`), `api/uploads.ts`, `api/aiReaderQueue.ts` — microservice-friendly; swap base URL per env.
- **Hooks:** `useToday`, `useUploadScans`, `useAiReaderQueue` — reusable, testable.
- **Types:** `types/index.ts` — `Page`, `AddSalesStep`, `AiReaderQueueItem`, `UploadScansResponse`.

### 1.1 Key Components

| Component | Purpose |
|-----------|---------|
| `App` | Root; composes `AppLayout` and page by route. |
| `AppLayout` | Header + Sidebar + main slot. |
| `Header` | Dealer name (centre), right slot (e.g. date). |
| `Sidebar` | Nav links; `onNavigate(page)`. |
| `AddSalesPage` | Aadhar field, tiles, `UploadScansPanel`. |
| `UploadScansPanel` | Step tiles, file input, upload button, uploaded list. |
| `AiReaderQueuePage` | Uses `useAiReaderQueue`; renders `AiReaderQueueTable`. |
| `PlaceholderPage` | Title + message for coming-soon pages. |

---

## 2. Backend (FastAPI) Structure (Modular / Microservice-Friendly)

### 2.1 Module Layout

```
backend/app/
  main.py           # App factory, CORS, include_router
  config.py         # DATABASE_URL, UPLOADS_DIR, APP_ROOT
  db.py             # get_connection()
  schemas/          # Pydantic request/response (e.g. uploads)
  repositories/     # Data access only (ai_reader_queue)
  services/         # Business logic (UploadService)
  routers/          # health, uploads, ai_reader_queue
```

- **Routers:** Thin; call services or repos. Each router can be mounted or split into a separate service later.
- **Services:** Stateless, injectable (e.g. `UploadService(uploads_dir=...)`).
- **Repositories:** Table access only; no business rules.

### 2.2 API Endpoints (Current)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness. |
| POST | `/uploads/scans` | Upload scans; enqueue to ai_reader_queue. |
| GET | `/ai-reader-queue` | List queue items (limit=200). |

### 2.3 Database Access

- **Connection:** `get_connection()` in `db.py` using `DATABASE_URL`.
- **Usage:** Context manager (`with get_connection() as conn`) in route handlers; cursor for execute/fetch.
- **Transactions:** Commit on success; explicit or context-based as we add more endpoints.

---

## 3. Database Schema (Current / Target)

### 3.1 Current

- **dealers:** `id` (SERIAL), `name` (TEXT), `city` (TEXT).

### 3.2 Target (Logical)

- **dealers** — id, name, city, (optional) code, created_at.
- **users** — id, dealer_id, email, role, created_at.
- **vehicles** — id, dealer_id, vin, make, model, year, etc.
- **customers** — id, dealer_id, name, contact info.
- **deals** — id, dealer_id, vehicle_id, customer_id, status, dates.
- **documents** — id, dealer_id, deal_id (optional), s3_key, type, created_at.
- **ocr_jobs** — id, document_id, status, result_json, error_message, created_at.
- **automation_jobs** — id, dealer_id, target_system, payload_ref, status, error_message, created_at.

All tenant-scoped tables include `dealer_id` for multi-tenant isolation.

---

## 4. Queue and Workers (Planned)

### 4.1 Queues

- **ocr_queue:** Message = { document_id, s3_key, options }.
- **automation_queue:** Message = { job_id, target_system, entity_refs }.

### 4.2 OCR Worker

- Poll or subscribe to ocr_queue; download file from S3; run Tesseract; parse and store result in DB; update `ocr_jobs` and document metadata.

### 4.3 Playwright Worker

- Poll or subscribe to automation_queue; load job; fetch related rows from DB; start browser; run site-specific script (login, navigate, fill, submit); update `automation_jobs` status and store artifacts in S3 if needed.

---

## 5. Configuration and Environment

- **Backend:** `.env` in `backend/` with `DATABASE_URL` (and later `REDIS_URL`, `AWS_*`, etc.).
- **Client:** Base API URL (env or config) for `fetch` calls.

---

## 6. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial LLD |
