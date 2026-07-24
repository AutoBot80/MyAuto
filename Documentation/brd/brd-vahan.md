# BRD — Vahan

**Version:** 2.2  
**Last Updated:** July 2026  
**Parent:** [README.md](README.md)

Vahan **workbench** automation: dealer-scoped batch, operator OTP/mobile assist, forms upload, scrape-back to queue and sale.

---

## 1. Business rules

| ID | Rule |
|----|------|
| V-BR-1 | Fill values from **`form_vahan_view`** only (**BR-9**) |
| V-BR-2 | Join **`insurance_master`** for financier/nominee context |
| V-BR-3 | Scrape `vahan_application_id`, RTO charges → `rto_queue` + `sales_master` |
| V-BR-4 | One active Vahan browser session per dealer |
| V-BR-5 | `in_queue=true` rows eligible for batch claim |
| V-BR-6 | Traces: `{mobile}_RTO.txt`, `Vahan_Form_Values.txt` |
| V-BR-7 | Tab reuse / warm-browser (**BR-12**) |
| V-BR-8 | HSRP / Dealer Registration Pendency Excel → append **`vahan_hsrp_holding`**; set **`vehicle_master.plate_num`** from Registration No when Chassis No matches and plate is not blank/`NEW`; files under `ocr_output/{dealer_id}/hsrp/` |

---

## 2. Queue statuses

| Status | Meaning | UI tab |
|--------|---------|--------|
| **Queued** | Ready for batch | In-process |
| **Pending** | Claimed / waiting | In-process |
| **In Progress** | Playwright active | In-process |
| **Failed** | Terminal error — Try Again | In-process |
| **Forms Missing** | Required upload docs absent | Forms Missing |
| **Completed** | Vahan cart/checkpoint reached | Completed |
| **Manually Completed** | Operator Mark Done | Completed |

---

## 3. RTO Queue page (client)

Three sub-tabs: **In-process**, **Forms Missing**, **Completed**.

### 3.1 In-process actions

| Action | API / sidecar |
|--------|---------------|
| Fill Vahan Site (two-step) | Warm → `POST /rto-queue/process-batch` or sidecar `fill_vahan_batch` |
| OTP entry | `POST /rto-queue/submit-operator-otp` |
| Mobile change | `POST /rto-queue/submit-operator-mobile-change` |
| In Queue toggle | `PATCH /rto-queue/{id}/in-queue` |
| Release stuck row | `POST /rto-queue/{id}/release` |
| Try Again | `POST /rto-queue/{id}/retry` |
| Mark Done | `POST /rto-queue/{id}/mark-done` |
| Batch status poll | `GET /rto-queue/process-batch/status` |

Batch processes up to **7** `in_queue` rows per dealer per run.

### 3.2 Forms Missing

| Action | API |
|--------|-----|
| Check readiness | `GET /rto-queue/{id}/forms-status` |
| Upload (Electron) | Sidecar `upload_rto_queue_forms` |
| Upload (dev API) | `POST /rto-queue/{id}/upload-forms` |
| Mark ready | `POST /rto-queue/{id}/forms-ready` → status Queued |

### 3.3 Completed

| Action | API |
|--------|-----|
| Requeue | `POST /rto-queue/{id}/requeue` |

---

## 4. Workbench fill (Screen 3)

PrimeFaces controls: `workbench_tabview:*`, MV Tax, hypothecation `hpa_*`, nominee radios, Save Vehicle Details, Save and File Movement.

Full page dump in log **only on terminal failure** (not every selector timeout).

---

## 5. Sidecar Vahan batch

| Job / endpoint | Purpose |
|----------------|---------|
| `fill_vahan_batch` | Local Playwright loop |
| `/sidecar/vahan/claim-batch` | Claim rows for dealer login |
| `/sidecar/vahan/row-result` | Per-row outcome + scrape |

---

## 6. Alternate mobile

Operator may set **`rto_queue.customer_mobile`** for Vahan OTP when different from `customer_master.mobile_number`. View exposes `COALESCE(queue mobile, customer mobile)`.

---

## 7. HSRP / Dealer Registration Pendency report

| Step | Behavior |
|------|----------|
| Download | Playwright: Report → Dealer Registration Pendency → Get Details → Yes → Download File → Excel |
| Save | `ocr_output/{dealer_id}/hsrp/vahan_hsrp_ddmmyyyy.xls` (same-day overwrite) |
| Holding | Append all Excel rows to **`vahan_hsrp_holding`** (dealer-scoped; manual truncate) |
| Plate apply | Update **`vehicle_master.plate_num`** where chassis matches and Registration No is not null/blank/`NEW` |

Service: `get_vahan_hsrp_report` / `load_hsrp_excel_to_holding` in `vahan_hsrp_report_service`. Local test: `Testing Wrappers/test_vahan_hsrp_report.py`.

---

## 8. APIs (summary)

| Method | Path |
|--------|------|
| GET | `/rto-queue?dealer_id=` |
| POST | `/rto-queue/process-batch` |
| GET | `/rto-queue/process-batch/status` |
| POST | `/fill-forms/vahan/warm-browser` |
| GET | `/customer-search/form-vahan` |

Legacy `POST /fill-forms/vahan` fill — **not implemented**; use process-batch.

**Electron / sidecar:** job ``vahan_hsrp_report`` downloads Excel on the dealer PC, then ``POST /sidecar/vahan/hsrp-report`` loads holding + plate_num (no local ``DATABASE_URL``). RTO Queue UI: **Download HSRP Report**.

---

## 9. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.2 | Jul 2026 | Sidecar ``vahan_hsrp_report`` + ``POST /sidecar/vahan/hsrp-report``; RTO Queue **Download HSRP Report** |
| 2.1 | Jul 2026 | V-BR-8 HSRP Excel → `vahan_hsrp_holding` + `vehicle_master.plate_num`; `ocr_output/.../hsrp/` |
| 2.0 | Jun 2026 | Full status model, OTP/mobile, forms missing, in_queue, sidecar batch, requeue/mark-done |
| 1.0 | Jun 2026 | Initial domain split |
