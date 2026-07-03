# BRD — Dealer Saathi

**Version:** 2.0  
**Last Updated:** June 2026  
**Parent:** [README.md](README.md)

Primary dealer workstation: authentication, document upload/OCR, Add Sales (three sub-tabs), customer/vehicle search, bulk PDF ingestion.

---

## 1. Authentication

| Requirement | Detail |
|-------------|--------|
| DS-AUTH-1 | `POST /auth/login` — email/password; returns JWT + dealer list when user has multiple dealer roles |
| DS-AUTH-2 | `GET /auth/me` — login_id, roles, home tile flags (`tile_pos`, `tile_rto`, `tile_dealer`, `tile_service`), admin flag |
| DS-AUTH-3 | Client stores JWT; all API calls use Bearer token (except `/health`, `/auth/login`) |
| DS-AUTH-4 | Dev bypass: `AUTH_DISABLED` / `VITE_AUTH_DISABLED` for local work |

---

## 2. Add Sales page structure

Three sub-tabs on **Add Sales**:

| Tab | Purpose |
|-----|---------|
| **New Sales** | Upload → review → Submit Info → Create Invoice → Generate Insurance → (optional CPA) → Print/Queue RTO |
| **In-process** | Resume open staging rows (7-day window, no RTO queue row yet) |
| **Invoices** | Committed sales list (15-day default); open sale documents |

Badge on **In-process** = row count from `GET /add-sales/in-process`.

---

## 3. New Sales workflow

### 3.1 Upload (Section 1)

| Input | API | Notes |
|-------|-----|-------|
| Per-file v2 | `POST /uploads/scans-v2` | Aadhaar, Details, optional Insurance/Financing |
| Consolidated PDF/images | `POST /uploads/scans-v2-consolidated` | Pre-OCR + Textract inline |
| Consolidated (SSE) | `POST /uploads/scans-v2-consolidated-stream` | Progress events |
| Manual page split | `POST /uploads/scans-v2-consolidated/manual-apply` | Operator assigns pages when classifier fails |

After upload: poll `GET /ai-reader-queue/extracted-details?subfolder=…`.

**Background warm (Electron):** sidecar opens DMS + insurance browsers after upload.

### 3.2 AI extracted information (Section 2)

Operator reviews/edits customer, vehicle, insurance blocks.

| Field source | Rules |
|--------------|-------|
| Aadhaar | AWS Textract only (scans-v2); gender default Male if unread (**BR-17**) |
| Details sheet | Profession/marital/insurer sanitizers; name reconcile with Aadhaar |
| CPA required | `insurance.cpa_reqd` from sheet + Section C; overrides dealer default for eligibility |
| Financier | Hero OEM + Bajaj prefix → staging **Hinduja** + UI note |

**Submit Info:** `POST /submit-info` → `{ ok, staging_id }` — draft `add_sales_staging` only.

**Staging patch (before/after automation):** `PATCH /add-sales/staging/{id}/payload` for operator corrections.

### 3.3 Fill forms (Section 3)

Gated until Submit succeeds. Sub-sections:

| Step | Button | Eligibility |
|------|--------|-------------|
| A. DMS | Create Invoice | `GET /add-sales/create-invoice-eligibility` — no sales row or blank invoice |
| B. Insurance | Generate Insurance | Invoice recorded; no non-empty Main `policy_num` |
| C. CPA Insurance | CPA Insurance | `cpa_alliance_portal_enabled`; `cpi_reqd=Y`; Hero CPI not blocking |
| D. Print | Print Forms and Queue RTO | Insurance step complete (or manual insurance state) |

**CPA context:** `GET /add-sales/dealer-cpa-context` — portal list, `hero_cpi`, dealer CPA insurer.

### 3.4 Print / Queue RTO pipeline (Electron)

`runPrintQueueRtoFlow`:

1. Pull sale scans from server (sidecar)
2. Dealer signature overlay (local)
3. Gate Pass PDF + silent print
4. Push sale folder to server (ZIP bundle for WAF)
5. `POST /rto-queue` — insert queue row
6. Failure → `process_failure_log` via sidecar

Browser-only dev: skips local pull/print/push; limited gate pass via API.

---

## 4. In-process tab

| API | Purpose |
|-----|---------|
| `GET /add-sales/in-process?dealer_id&days=7` | Open staging rows |
| `GET/PATCH /add-sales/staging/{id}/payload` | Load/edit payload |
| Same automation as New tab | Per-row Create Invoice, GI, CPA, Print/RTO |

Editable: care_of, address, frame/engine/key/battery, nominee, insurer, `cpi_reqd`.

---

## 5. Invoices tab

| API | Purpose |
|-----|---------|
| `GET /add-sales/invoices?dealer_id&days=15` | Committed sales |
| Optional filters | `mobile`, `chassis`, `engine` |
| Documents | `GET /documents/{subfolder}/list` |

---

## 6. View Customers / View Vehicles

| Page | API | Features |
|------|-----|----------|
| View Customers | `GET /customer-search/search` | Masters, sales, insurance; `form-vahan` row; document list; Print File (Electron) |
| View Vehicles | `GET /vehicle-search/search` | Wildcard chassis/engine; inventory + challan lines |

---

## 7. Bulk Loads

| Step | Behaviour |
|------|-----------|
| Input | `Bulk Upload/<dealer>/Input Scans/` |
| Hot table | `bulk_loads` — Queued → Processing → terminal |
| Pre-OCR | `raw/page_NN.pdf`, normalized JPEGs (**BR-23**, **BR-24**) |
| Failure | Error/Rejected until `action_taken=true` (**BR-8**) |
| Reprocess | `POST /bulk-loads/{id}/prepare-reprocess` → Add Sales prefill |

**Visibility:** POS tab only for login `shashank`.

---

## 8. Dealer mode (separate tile)

| Page | API |
|------|-----|
| Dealer Dashboard | `GET /dealers/{id}/dashboard/summary`, subdealer matrices, challan drill-downs |
| Sales Reports | Invoices + committed challan lists; Excel export |

---

## 9. Staging processing states

| Column | Values | Meaning |
|--------|--------|---------|
| `dms_state` | 0, 1, 2 | Not started → vehicle prep committed → customer prep committed |
| `insurance_state` | 0, 2, 3 | Not started → preview/manual issue (print resume) → GI complete |
| `cpi_reqd` | Y/N | CPA section eligibility on staging row |

Updated via sidecar `POST /sidecar/staging/processing-state` during automation.

---

## 10. Functional requirements (summary)

- **FR-1–7** Client UI, upload, OCR review, job status, validation
- **FR-8** Add Sales flow with three sub-tabs and eligibility gates
- **FR-10** View Customer + Vahan projection row
- **FR-17** Submit Info → staging only; masters after Create Invoice
- **FR-22–24** Search, QR, Vision/Textract helpers
- **FR-26** Bulk pre-OCR pipeline

---

## 11. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Jun 2026 | Auth, In-process/Invoices tabs, consolidated upload, print pipeline, staging states, Dealer mode |
| 1.0 | Jun 2026 | Initial domain split |
