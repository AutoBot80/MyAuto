# Low Level Design (LLD)
## Auto Dealer Management System

**Version:** 0.1  
**Last Updated:** March 2025

---

## 1. Client (React) Structure

- **Top section:** Header with dealership name (e.g. "Arya Agencies") and current date.
- **Screens/views:** Dealers list and form; later: documents upload, job status, review/extracted data, automation triggers.
- **Validation:** Required fields, basic format checks; no business rules (e.g. pricing) in client.
- **API usage:** `fetch` to backend base URL (configurable); errors surfaced to user simply.

### 1.1 Key Components (Current / Planned)

| Component | Purpose |
|-----------|---------|
| `App` | Root layout, header, main content area. |
| Dealers list/form | List dealers, add dealer (POST/GET `/dealers`). |
| (Future) Document upload | Upload file, show job id and status. |
| (Future) Job status | Poll or display job status for OCR and automation. |

---

## 2. Backend (FastAPI) Structure

### 2.1 Module Layout

```
backend/
  app/
    main.py       # App factory, CORS, route registration
    config.py     # Env (e.g. DATABASE_URL) via dotenv
    db.py         # PostgreSQL connection helper
    routers/      # (Planned) auth, dealers, vehicles, documents, jobs
    models/       # (Planned) Pydantic request/response models
    services/     # (Planned) business logic, job enqueue
```

### 2.2 API Endpoints (Current / Planned)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness. |
| GET | `/dealers` | List dealers. |
| POST | `/dealers` | Create dealer. |
| (Planned) | `/vehicles`, `/customers`, `/deals`, `/documents`, `/jobs` | CRUD and job triggers. |

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
