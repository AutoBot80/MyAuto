# Low Level Design (LLD)
## Auto Dealer Management System

**Version:** 3.1  
**Last Updated:** July 2026  
**BRD:** [brd/README.md](brd/README.md)  
**Siebel/Insurance deep notes:** [low-level-design-v1.24-archive.md](low-level-design-v1.24-archive.md)

---

## 1. Conventions

- **Base URL:** No `/api` prefix; routers mounted at app root in `main.py`
- **Auth:** Bearer JWT (`Authorization` header); exceptions: `/health`, `/auth/login`
- **Admin:** `/admin/*` requires admin role; dealer scope via `admin_dealer_access_ref`
- **Sidecar:** Desktop sends `api_url` + JWT; no direct Postgres on dealer PC
- **Traces:** `ocr_output/<dealer_id>/<subfolder>/`
- **Date format:** dd/mm/yyyy in app and DB varchar date fields

---

## 2. Shared

### 2.1 Health & settings

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + backend version/commit |
| GET | `/settings/site-urls` | DMS/Vahan/Insurance URLs, production flag |

### 2.2 Auth

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/login` | JWT; multi-dealer list when applicable |
| GET | `/auth/me` | Profile, roles, tile flags |

### 2.3 System

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/system/teardown-local-browsers` | Kill managed Chromium (dev/sidecar) |

### 2.4 Config (selected)

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL |
| `ENVIRONMENT` | prod gates (e.g. Create Invoice auto-click) |
| `STORAGE_BACKEND`, `S3_*` | local vs S3 |
| `JWT_*`, `AUTH_DISABLED` | Auth |
| `DMS_BASE_URL`, `DMS_REAL_URL_*`, `DMS_SIEBEL_*` | Siebel |
| `INSURANCE_BASE_URL`, `HERO_MISP_*`, `KYC_*` | MISP |
| `VAHAN_BASE_URL` | Vahan |
| `PRE_OCR_*`, `BULK_*` | Bulk pipeline |
| `SAATHI_BASE_DIR`, `UPLOADS_DIR`, `OCR_OUTPUT_DIR` | Paths |

Startup: `validate_external_site_urls()` requires DMS + Insurance URLs; rejects `DMS_MODE=dummy`.

### 2.5 Sidecar jobs (`electron/sidecar/job_runner.py`)

| Job type | Purpose |
|----------|---------|
| `warm_browser`, `warm_insurance`, `warm_cpa`, `warm_vahan` | Pre-open portals |
| `fill_dms` | Full DMS via `/sidecar/dms/*` |
| `fill_insurance` | Hero GI via `/sidecar/insurance/*` |
| `fill_cpa_alliance_insurance` | CPA via `/sidecar/cpa/*` |
| `fill_vahan_batch` | RTO batch |
| `fill_subdealer_challan` | Local challan DMS |
| `upload_rto_queue_forms` | Vahan category uploads |
| `print_gate_pass_local` | Local Gate Pass PDF |
| `push_sale_artifacts`, `pull_sale_scan_assets` | Folder sync |
| `push_sale_bundle` | ZIP upload for print/RTO |
| `dealer_sign_overlay` | Signature on PDFs |
| `upload_sale_artifacts`, `mirror_challan_parse_artifacts` | Artifact sync |
| `upload_print_rto_queue_log` | Print log upload |
| `teardown_local_browsers` | Browser cleanup |

Hot-sync: `GET /sidecar/scripts/bundle` at job start.

---

## 3. Dealer Saathi

### 3.1 Client structure

```
client/src/
  pages/          AddSalesPage, ViewCustomerPage, BulkLoadsPage, …
  api/            addSales.ts, uploads.ts, submitInfo.ts, …
  utils/          printQueueRtoFlow.ts, electronUploadScanMirror.ts
  hooks/          useUploadScans, usePageVisible
```

**Add Sales sub-components:** `AddSalesInProcessPanel`, `AddSalesInvoicesPanel`

**Page type:** `add-sales | subdealer-challan | bulk-loads | customer-details | view-vehicles | rto-status | dealer-dashboard | sales-reports | admin-*`

### 3.2 Upload & OCR APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/uploads/scans` | Legacy upload |
| POST | `/uploads/scans-v2` | Per-sale folder upload + inline OCR |
| POST | `/uploads/scans-v2-consolidated` | Consolidated PDF/images |
| POST | `/uploads/scans-v2-consolidated-stream` | SSE progress |
| POST | `/uploads/scans-v2-consolidated/manual-apply` | Manual page assignments |
| GET | `/uploads/manual-session/{id}/page/{n}` | Manual split preview |
| GET | `/ai-reader-queue/extracted-details` | Structured OCR JSON |
| GET | `/ai-reader-queue/insurance-extraction` | Debug insurance OCR |
| POST | `/textract/extract`, `/extract-forms` | Ad-hoc Textract |
| POST | `/vision/aadhar-analyze` | OpenAI vision |
| POST | `/qr-decode` | Aadhaar QR subprocess |

### 3.3 Submit & staging APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/submit-info` | Draft `add_sales_staging` → `staging_id` |
| GET | `/add-sales/in-process` | Open staging rows (7d) |
| GET | `/add-sales/invoices` | Committed sales (15d) |
| GET | `/add-sales/staging/{id}/payload` | Read payload + states |
| PATCH | `/add-sales/staging/{id}/payload` | Merge operator edits |
| GET | `/add-sales/dealer-cpa-context` | CPA portal list, hero_cpi |
| GET | `/add-sales/create-invoice-eligibility` | CI/GI/CPA gates |

### 3.4 Staging table

| Column | Notes |
|--------|-------|
| `staging_id` | UUID PK |
| `payload_json` | customer, vehicle, insurance, file_location |
| `status` | draft / committed / abandoned |
| `dms_state` | 0 / 1 / 2 |
| `insurance_state` | 0 / 2 / 3 |
| `cpi_reqd` | Y/N |
| `login_id`, `subfolder` | Operator + folder |

**Commit wave:** `customer_master` → `vehicle_master` → `sales_master` → trigger reminders only.

Services: `submit_info_service`, `add_sales_commit_service`, `add_sales_staging_patch_service`, `add_sales_staging_state_service`.

### 3.5 Bulk loads

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/bulk-loads` | List hot rows |
| GET | `/bulk-loads/counts`, `/pending-count` | Badges |
| PATCH | `/bulk-loads/{id}/action-taken` | Mark addressed |
| PATCH | `/bulk-loads/{id}/mark-success` | Manual success |
| POST | `/bulk-loads/{id}/prepare-reprocess` | Re-queue |
| POST | `/bulk-loads/reset-stale-processing` | Recover stuck |
| DELETE | `/bulk-loads` | Clear dealer rows |
| GET | `/bulk-loads/folder/*`, `/file/*` | Browse/download |

Pre-OCR: `pre_ocr_service.run_pre_ocr_and_prepare` — `raw/page_NN.pdf`, normalized JPEGs outside `raw/`.

### 3.6 Search & documents

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/customer-search/search` | Mobile/plate |
| GET | `/customer-search/form-vahan` | Vahan display row |
| GET | `/vehicle-search/search` | Chassis/engine |
| GET | `/documents/{subfolder}/list` | Sale folder files |
| GET | `/documents/{subfolder}/{filename}` | Download scan |
| GET | `/dealers/{id}/dashboard/*` | Dealer dashboard widgets |

### 3.7 OCR services (key behaviours)

- Aadhaar: Textract only on scans-v2; gender default Male
- Details: profession/marital/insurer sanitizers; name reconcile
- `ocr_run_log_service`: log missing fields for admin

---

## 4. DMS

### 4.1 Fill APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/fill-forms/dms` | DMS-only Playwright |
| POST | `/fill-forms` | Legacy full fill |
| POST | `/fill-forms/dms/warm-browser` | Open DMS tab |
| GET | `/fill-forms/data-from-dms` | Parse DMS.txt |

### 4.2 Sidecar DMS

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/sidecar/dms/resolve` | Fill values from staging |
| POST | `/sidecar/dms/vehicle-after-prepare` | dms_state=1 |
| POST | `/sidecar/dms/customer-after-prepare` | dms_state=2 |
| POST | `/sidecar/dms/commit` | Masters after Invoice# |

### 4.3 Services

| Service | Role |
|---------|------|
| `fill_hero_dms_service` | Orchestrator |
| `hero_dms_prepare_customer` | Contact Find → payments |
| `hero_dms_playwright_vehicle` | prepare_vehicle, PDI |
| `hero_dms_playwright_invoice` | prepare_order, attach, reports |
| `hero_dms_db_service` | Persist masters |
| `hero_dms_reports_service` | GST Run Report PDFs |
| `form_dms.py` | Inline SQL fill row (no view) |
| `handle_browser_opening` | CDP tab reuse |

**Siebel parity table:** see archive §2.4d.

---

## 5. Subdealer Challans

### 5.1 APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/subdealer-challan/parse-scan` | DDR OCR |
| POST | `/subdealer-challan/staging` | Create batch |
| POST | `/subdealer-challan/process/{batch_id}` | Server batch |
| GET | `/subdealer-challan/staging/recent` | Processed tab |
| GET | `/subdealer-challan/staging/failed-count` | Badge |
| PATCH | `/subdealer-challan/staging/detail/{id}` | Fix line |
| PATCH | `/subdealer-challan/staging/master/{batch_id}` | Change to_dealer |
| POST | `…/retry`, `…/retry-order` | Retries |
| GET | `/subdealer-challan/invoices/recent` | Committed list |
| GET | `/subdealer-challan/invoices/{id}/details` | Lines |

### 5.2 Sidecar

| Method | Path |
|--------|------|
| POST | `/sidecar/subdealer-challan/resolve` |
| POST | `/sidecar/subdealer-challan/prepare-result` |
| POST | `/sidecar/subdealer-challan/order-context` |
| POST | `/sidecar/subdealer-challan/order-checkpoint` |
| POST | `/sidecar/subdealer-challan/finalize-order` |
| POST | `/sidecar/subdealer-challan/requeue-failed` |

### 5.3 Discount resolution

`vehicle_inventory.get_subdealer_challan_discount(from, to, model)` + `line_discount_after_transport` (transport cost + `reduce_discount_by_percent`).

---

## 6. Insurance and CPA

### 6.1 APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/fill-forms/insurance/hero` | Hero MISP |
| POST | `/fill-forms/insurance/cpa-alliance` | CPA portal (cloud) |
| POST | `/fill-forms/insurance/warm-browser` | Warm tab |

### 6.2 Sidecar

| Method | Path |
|--------|------|
| POST | `/sidecar/insurance/resolve` |
| POST | `/sidecar/insurance/commit` |
| POST | `/sidecar/cpa/resolve` |
| POST | `/sidecar/cpa/commit` |
| POST | `/sidecar/staging/processing-state` |

### 6.3 Services & views

| Artifact | Role |
|----------|------|
| `fill_hero_insurance_service` | pre_process + main_process |
| `hero_insure_reports_service` | Print Policy PDF |
| `insurance_form_values.build_insurance_fill_values` | View + staging merge |
| `add_alliance_cpa_insurance` | CPA Playwright |
| `cpa_form_values` | CPA fill from `form_cpa_insurance_view` |
| `form_insurance_view` | Main GI projection |
| `form_cpa_insurance_view` | CPA projection |

**`insurance_master.insurance_type`:** Main | CPA; unique with year.

---

## 7. Print / Queue RTO

### 7.1 Form generation

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/fill-forms/print-form20` | Form 20 PDFs |
| POST | `/fill-forms/print-gate-pass` | Gate Pass PDF |
| GET | `/fill-forms/form20-status` | Template check |
| GET | `/sidecar/gate-pass-context` | Local gate pass data |
| GET | `/sidecar/templates/gate-pass-docx` | Template download |

### 7.2 Queue insert

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/rto-queue` | Insert/update row |
| GET | `/rto-queue/by-sale` | By sale IDs |

### 7.3 Sidecar print pipeline

| Job / endpoint | Purpose |
|----------------|---------|
| `pull_sale_scan_assets` | Local copy |
| `dealer_sign_overlay` | PDF signatures |
| `print_gate_pass_local` | Gate pass |
| `push_sale_bundle` | `/sidecar/push-sale-bundle` |
| `upload_print_rto_queue_log` | Trace upload |

Client: `utils/printQueueRtoFlow.ts`

---

## 8. Vahan

### 8.1 Queue APIs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/rto-queue?dealer_id=` | List all rows |
| POST | `/rto-queue/process-batch` | Start batch (≤7) |
| GET | `/rto-queue/process-batch/status` | Progress + OTP pending |
| POST | `/rto-queue/submit-operator-otp` | Deliver OTP |
| POST | `/rto-queue/submit-operator-mobile-change` | Change mobile mid-run |
| GET | `/rto-queue/{id}/forms-status` | Doc readiness |
| POST | `/rto-queue/{id}/forms-ready` | Forms Missing → Queued |
| PATCH | `/rto-queue/{id}/in-queue` | Toggle batch eligibility |
| POST | `/rto-queue/{id}/release` | Unstick In Progress |
| POST | `/rto-queue/{id}/upload-forms` | Dev multipart upload |
| POST | `/rto-queue/{id}/mark-done` | Manual complete |
| POST | `/rto-queue/{id}/requeue` | Completed → Queued |
| POST | `/rto-queue/{id}/retry` | Failed → Queued |
| POST | `/fill-forms/vahan/warm-browser` | Warm Vahan |

### 8.2 Sidecar

| Method / job | Purpose |
|--------------|---------|
| `fill_vahan_batch` | Local batch |
| `/sidecar/vahan/claim-batch` | Claim rows |
| `/sidecar/vahan/row-result` | Outcome + scrape |
| `vahan_hsrp_report` | Local Excel download → `POST /sidecar/vahan/hsrp-report` (holding + plate_num) |
| `upload_rto_queue_forms` | Category uploads |

### 8.3 Services

| Service | Role |
|---------|------|
| `fill_rto_service.fill_rto_row` | Workbench Screen 3 |
| `rto_payment_service` | Batch loop, dealer advisory lock |
| `rto_otp_bridge` | OTP/mobile operator bridge |
| `form_vahan.py` | Read `form_vahan_view` |
| `vahan_hsrp_report_service.get_vahan_hsrp_report` | Report → Excel under `ocr_output/{dealer_id}/hsrp/`; append `vahan_hsrp_holding`; update `vehicle_master.plate_num` by chassis |
| `vahan_hsrp_report_service.load_hsrp_excel_to_holding` | Parse `.xls` (xlrd) → INSERT holding → plate UPDATE |

Workbench selectors: `workbench_tabview:*`, `hpa_*`, `nomineeradiobtn1:*`. Log dump on terminal failure only — archive §2.4f.

HSRP flow never `goto`/reloads the Vahan tab (single-session guard). Login gate is report-specific (any post-login shell), not RTO `officeList`.

---

## 9. Admin Saathi

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/dealers` | Scoped list |
| POST | `/admin/dealers` | Create |
| GET/PATCH | `/admin/dealers/{id}` | Detail/update |
| GET | `/admin/dealers/{id}/logins` | Login assignments |
| PUT | `/admin/dealers/{id}/login-assignments/upsert` | Bulk upsert |
| POST | `/admin/dealers/{id}/login-roles` | Add role |
| GET/POST | `/admin/dealers/{id}/discounts` | Subdealer discounts |
| GET | `/admin/roles`, `/admin/login-catalog` | Catalogs |
| PATCH | `/admin/logins/{id}/active-flag` | Enable/disable |
| GET | `/admin/usage-dealer-matrix` | 7d matrix |
| GET | `/admin/failure-logs`, `/admin/ocr-logs` | Diagnostics |
| GET | `/admin/folder-contents`, `/folder-file`, `/folder-zip` | Browse/download |
| GET | `/admin/data-folders` | Path roots |
| GET | `/admin/staging/search`, `/admin/staging/{id}` | Staging admin |
| POST | `/admin/staging/{id}/cancel-invoice` | Rollback |
| POST | `/admin/staging/{id}/insurance-manually-filled` | insurance_state=2 |
| POST | `/admin/reset-all-data` | Truncate (non-prod) |
| GET | `/admin/portal-insurers` | Insurer dropdown |

Services: `admin_staging_cancel_invoice_service`, `admin_staging_insurance_manual_service`, `process_failure_log_service`, `ocr_run_log_service`, `dealer_storage`.

---

## 10. Database schema summary

See **Database DDL.md** (v3.0). Key objects by domain:

| Domain | Tables / views |
|--------|----------------|
| Shared ref | `oem_ref`, `dealer_ref`, `master_ref`, `roles_ref`, `login_ref`, `login_roles_ref`, `subdealer_discount_master_ref`, `admin_dealer_access_ref` |
| Dealer Saathi | `customer_master`, `vehicle_master`, `sales_master`, `add_sales_staging`, `bulk_loads`, `ai_reader_queue` |
| Insurance | `insurance_master`, `form_insurance_view`, `form_cpa_insurance_view` |
| Print/RTO | `rto_queue`, `service_reminders_queue`, `rc_status_sms_queue` |
| Vahan | `form_vahan_view`, **`vahan_hsrp_holding`** |
| Subdealer | `challan_*`, `vehicle_inventory_master` |
| Admin | `process_failure_log`, `ocr_run_log` |

DMS fill: `form_dms.py` — **`form_dms_view` dropped**.

---

## 11. Sidecar proxy (full list)

| Method | Path |
|--------|------|
| POST | `/sidecar/dms/resolve`, `vehicle-after-prepare`, `customer-after-prepare`, `commit` |
| POST | `/sidecar/insurance/resolve`, `commit` |
| POST | `/sidecar/cpa/resolve`, `commit` |
| POST | `/sidecar/staging/processing-state` |
| POST | `/sidecar/vahan/claim-batch`, `row-result` |
| POST | `/sidecar/vahan/hsrp-report` |
| POST | `/sidecar/failure-log` |
| POST | `/sidecar/upload-artifacts` |
| POST | `/sidecar/push-sale-bundle`, `sync-sale-folder-s3` |
| POST | `/sidecar/subdealer-challan/*` (6 endpoints) |
| GET | `/sidecar/scripts/version`, `scripts/bundle`, `gate-pass-context`, `templates/gate-pass-docx` |

---

## 12. Document control

| Version | Date | Changes |
|---------|------|---------|
| 3.1 | Jul 2026 | `vahan_hsrp_report_service`: HSRP Excel → `vahan_hsrp_holding` → `vehicle_master.plate_num`; `ocr_output/.../hsrp/`; sidecar job + `POST /sidecar/vahan/hsrp-report`; **DDL 3.09** |
| 3.0 | Jun 2026 | Full codebase sync: ~130 routes, sidecar jobs, auth, Add Sales tabs, RTO lifecycle, admin, CPA insurance_type |
| 2.0 | Jun 2026 | Domain restructure |
| 1.24 | Apr 2026 | Monolithic LLD (archived) |
