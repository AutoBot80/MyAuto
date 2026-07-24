# High Level Design (HLD)
## Auto Dealer Management System

**Version:** 3.1  
**Last Updated:** July 2026  
**BRD:** [brd/README.md](brd/README.md)

---

## 1. System context

```
  ┌─────────────────────────────────────────────────────────┐
  │              Electron Desktop (Dealer Saathi)              │
  │  React UI │ sidecar (Playwright) │ silent print │ files  │
  └───────────────────────────┬─────────────────────────────┘
                              │ HTTPS + JWT
                              ▼
  ┌─────────────────────────────────────────────────────────┐
  │                    FastAPI (saathi-api)                  │
  │  Routers │ Services │ Repositories │ OCR │ bulk worker   │
  └───────┬─────────────────────────────┬───────────────────┘
          │                             │
          ▼                             ▼
   PostgreSQL (RDS)              Local disk / S3
   staging, masters, queue       uploads, ocr_output, challans
```

**Dual automation runtime:** Electron sidecar runs Playwright locally and persists via **`/sidecar/*`** DB proxy. Cloud **`/fill-forms/*`** serves browser-only dev and long-running API-host jobs (e.g. subdealer process on server).

---

## 2. Shared infrastructure

### 2.1 Components

| Component | Responsibility |
|-----------|----------------|
| **Electron shell** | App packaging, IPC, sidecar job runner, silent PDF print |
| **React client** | POS / RTO / Dealer / Admin modes; API + sidecar wrappers |
| **FastAPI** | REST, JWT auth, OCR, staging, admin, sidecar proxy |
| **PostgreSQL** | System of record |
| **S3 (optional)** | `STORAGE_BACKEND=s3` — uploads, OCR, challans |
| **Sidecar** | `electron/sidecar/job_runner.py` — 22 job types |

### 2.2 Repository layout

```
My Auto.AI/
├── backend/app/          # FastAPI
├── client/src/           # React
├── electron/             # main, preload, sidecar
├── DDL/                  # PostgreSQL scripts
└── Documentation/brd/    # Domain BRDs
```

### 2.3 Auth

- `POST /auth/login` → JWT
- `GET /auth/me` → roles + home tile flags
- Middleware on all routes except `/health`, `/auth/login`
- Admin routes: `require_admin` + `admin_dealer_access_ref` scope

### 2.4 Deployment

| Target | Notes |
|--------|-------|
| Dealer PC | Electron installer; sidecar + local Chromium |
| AWS EC2 | FastAPI + RDS; SSM deploy via `Update-Prod-App-Backend.ps1` |
| S3 | Optional artifact storage when `STORAGE_BACKEND=s3` |

### 2.5 Design decisions

| Decision | Rationale |
|----------|-----------|
| Sidecar + `/sidecar/*` | Desktop Playwright without DB credentials on PC |
| `add_sales_staging` | Survive long DMS runs; resume In-process tab |
| DB-first automation fills | Auditable; no invented portal values |
| One Vahan batch lock per dealer | Matches desk workflow |
| `insurance_type` Main/CPA | Separate policies per channel |
| Hot-sync script bundle | Sidecar pulls `backend/app` at job start |

---

## 3. Dealer Saathi

**BRD:** [brd-dealer-saathi.md](brd/brd-dealer-saathi.md)

### 3.1 Client

| Page | Mode | Key features |
|------|------|--------------|
| `LoginPage` | Boot | Multi-dealer picker |
| `HomePage` | Home | Tiles; Release Browsers; Silent Print toggle |
| `AddSalesPage` | POS | New / In-process / Invoices sub-tabs |
| `ViewCustomerPage` | POS, Dealer | Search; Vahan row; Print File (Electron) |
| `ViewVehiclesPage` | POS, Dealer | Chassis/engine search |
| `BulkLoadsPage` | POS† | Hot table; reprocess |
| `DealerDashboardPage` | Dealer | 7-day metrics |
| `SalesReportsPage` | Dealer | Export invoices/challans |

† login `shashank` only.

### 3.2 Backend modules

| Module | Role |
|--------|------|
| `auth` | Login, me |
| `uploads`, `pre_ocr_service`, `post_ocr_service` | scans-v2, consolidated, manual split |
| `sales_ocr_service`, `sales_textract_service` | Textract merge |
| `submit_info_service`, `add_sales_*` | Staging CRUD, eligibility |
| `bulk_*` | Ingest + worker |
| `customer_search`, `vehicle_search` | Lookup |
| `dealers` (dashboard) | Dealer-mode widgets |

### 3.3 Data flows

**Add Sales:** upload → OCR → Submit (`staging_id`) → DMS → GI → CPA? → Print/RTO queue

**Bulk:** Input Scans → queue → pre-OCR → Add Sales pipeline

---

## 4. DMS

**BRD:** [brd-dms.md](brd/brd-dms.md)

| Layer | Modules |
|-------|---------|
| Cloud | `fill_hero_dms_service`, `hero_dms_playwright_*`, `hero_dms_db_service`, `hero_dms_reports_service` |
| Sidecar jobs | `fill_dms`, `warm_browser` |
| Sidecar API | `/sidecar/dms/resolve`, `vehicle-after-prepare`, `customer-after-prepare`, `commit` |

**Outputs:** masters commit, `Playwright_DMS_*.txt`, optional GST PDFs, `dms_state` on staging.

---

## 5. Subdealer Challans

**BRD:** [brd-subdealer-challans.md](brd/brd-subdealer-challans.md)

| Layer | Modules |
|-------|---------|
| Client | `SubdealerChallanPage`, `api/subdealerChallan.ts` |
| API | `subdealer_challan` router, `add_subdealer_challan_service` |
| Sidecar | `fill_subdealer_challan`, `/sidecar/subdealer-challan/*` |

**Tables:** `challan_*_staging`, `challan_master`, `challan_details`, `vehicle_inventory_master`, `subdealer_discount_master_ref`.

---

## 6. Insurance and CPA

**BRD:** [brd-insurance-and-cpa.md](brd/brd-insurance-and-cpa.md)

| Layer | Modules |
|-------|---------|
| Hero GI | `fill_hero_insurance_service`, `hero_insure_reports_service` |
| CPA | `add_alliance_cpa_insurance`, `cpa_form_values` |
| Views | `form_insurance_view`, `form_cpa_insurance_view` |
| Sidecar | `fill_insurance`, `fill_cpa_alliance_insurance` |

**Table:** `insurance_master` with `insurance_type` ∈ {Main, CPA}.

---

## 7. Print / Queue RTO

**BRD:** [brd-print-queue-rto.md](brd/brd-print-queue-rto.md)

| Module | Role |
|--------|------|
| `form20_service` | Form 20 + Gate Pass PDF |
| `printRtoSidecar` (client) | Pull, sign overlay, gate pass print, push bundle |
| `rto_payment_details` router | Queue insert (from print step) |

**Sidecar:** `print_gate_pass_local`, `push_sale_bundle`, `upload_print_rto_queue_log`.

---

## 8. Vahan

**BRD:** [brd-vahan.md](brd/brd-vahan.md)

| Module | Role |
|--------|------|
| `fill_rto_service` | Workbench row fill |
| `rto_payment_service` | Batch loop, dealer lock |
| `rto_otp_bridge` | Operator OTP/mobile bridge |
| `vahan_hsrp_report_service` | Dealer Registration Pendency Excel (sidecar: local download → cloud `hsrp-report`) → `vahan_hsrp_holding` → `vehicle_master.plate_num` |
| Sidecar | `fill_vahan_batch`, `upload_rto_queue_forms` |

**View:** `form_vahan_view`. **Tables:** `rto_queue` (status lifecycle, `in_queue`); **`vahan_hsrp_holding`** (append-only HSRP Excel). Artifacts: `ocr_output/{dealer_id}/hsrp/`.

---

## 9. Admin Saathi

**BRD:** [brd-admin-saathi.md](brd/brd-admin-saathi.md)

| Module | Role |
|--------|------|
| `admin` router | Dealers, usage, folders, logs, reset |
| `admin_staging_*_service` | Cancel invoice, manual insurance |
| `process_failure_log_service`, `ocr_run_log_service` | Diagnostics |
| `dealer_storage` | S3/local folder browse |

**Scope:** `admin_dealer_access_ref`.

---

## 10. External integrations

| System | Integration |
|--------|-------------|
| Hero Connect / Siebel | Playwright DMS |
| Hero MISP | Playwright insurance |
| CPA Alliance portals | Playwright CPA |
| Vahan workbench | Playwright RTO |
| AWS Textract | OCR |
| Tesseract | Pre-OCR, OSD |

**Required env at startup:** `DMS_BASE_URL`, `INSURANCE_BASE_URL`.

---

## 11. Document control

| Version | Date | Changes |
|---------|------|---------|
| 3.1 | Jul 2026 | Vahan HSRP report service → holding table + plate_num; `ocr_output/.../hsrp/` |
| 3.0 | Jun 2026 | Full codebase refresh: auth, sidecar, Add Sales tabs, RTO lifecycle, admin, CPA, Electron |
| 2.0 | Jun 2026 | Domain section restructure |
| 1.173 | May 2026 | Last monolithic HLD |
