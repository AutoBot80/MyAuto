# BRD — DMS

**Version:** 2.0  
**Last Updated:** June 2026  
**Parent:** [README.md](README.md)

Hero Connect / Siebel automation for **Create Invoice**: contact find, enquiry, vehicle prep, booking, payments, invoice, optional Run Report PDFs, master commit.

---

## 1. Execution paths

| Path | When | Entry |
|------|------|-------|
| **Electron sidecar** | Production desktop | Sidecar job `fill_dms` → `/sidecar/dms/resolve`, checkpoints, `/sidecar/dms/commit` |
| **Cloud API** | Browser dev / fallback | `POST /fill-forms/dms` or `POST /fill-forms` |
| **Warm browser** | After upload | `POST /fill-forms/dms/warm-browser` or sidecar `warm_browser` |

Sidecar checkpoints persist **`dms_state`** on staging: **1** after vehicle prep, **2** after customer prep (**BR-26**).

---

## 2. Business rules

| ID | Rule |
|----|------|
| DMS-BR-1 | Fill from `add_sales_staging.payload_json` + Siebel scrape — not live master reads on staging path |
| DMS-BR-2 | No invented field values (**BR-13**) |
| DMS-BR-3 | Create Invoice: Apply Campaign → Create Invoice; scrape Invoice# |
| DMS-BR-4 | Training budget constant 89000 for dummy booking paths |
| DMS-BR-5 | Ex-showroom → `vehicle_master.vehicle_ex_showroom_price` |
| DMS-BR-6 | Contact Find + Add Enquiry normative rules (**§5**) |
| DMS-BR-7 | Post-commit Run Reports: GST Retail Invoice, GST Booking Receipt |
| DMS-BR-8 | Traces: `DMS_Form_Values.txt`, `Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt` (IST) |
| DMS-BR-9 | Real Siebel: always Contact Find first; `skip_find` in DB ignored |
| DMS-BR-10 | Do not overwrite `raw_frame_num` / `raw_engine_num` on merge |
| DMS-BR-11 | Hero OEM only (`oem_id = 1`) unless product extends |

---

## 3. UI / operator flow

| Control | API / job | Notes |
|---------|-----------|-------|
| Create Invoice | `fillDmsLocal` / `POST /fill-forms/dms` | Body: `staging_id`, `subfolder`, `dealer_id` |
| Warm browser | warm-browser / sidecar | After upload |
| Recovery | `GET /fill-forms/data-from-dms` | Read `Data from DMS.txt` if present |

Response: `dms_milestones`, `dms_step_messages`, `customer_id`, `vehicle_id`, optional `hero_dms_form22_print`.

---

## 4. Siebel sequence (target — §6.1a)

| Step | Action |
|------|--------|
| 0 | Logged-in Hero Connect session (CDP reuse) |
| 1 | Contact Find: Mobile + Contact First Name (required) |
| 2a | No match → vehicle find → Enquiry / Opportunities New |
| 2b | Match → open enquiry or suffixed first name; care-of; branch (2) address/postal if no open enquiry |
| 3 | Vehicles: Find→Vehicles; Title drilldown; Key/Battery; In Transit vs dealer stock |
| 4a | In Transit → Process Receipt only (no Pre-check/PDI) |
| 4b | Dealer stock → Generate Booking; My Orders mobile grid; multi-line attach; Price All / Allocate All |
| 5 | Operator gates (OTP, finance, hypothecation) |
| 6 | Create Invoice; optional Run Report batch; browser left open |

**Implementation parity:** LLD §3.3 / archive §2.4d.

---

## 5. Customer (Contact Find — normative)

1. Same-frame Mobile + First Name (`field_textbox_1`); exact first name for Find query
2. Grid match: mobile + fuzzy first-token; mobile-only fallback
3. Duplicate rows: in-place drill; Contact_Enquiry open enquiry detection
4. No open enquiry: suffixed first name (`.`, `..`, …)
5. Enquiry# gate after Ctrl+S: must change within 0.5s / 2.5s / 3.5s
6. Video branch (2): Home Phone, Email, Address tab, City LOV, Postal Code, Ctrl+S
7. Payments: Third Level View Bar → Payments; Save / Ctrl+S; Transaction# required

---

## 6. Vehicle

- **`prepare_vehicle`:** Auto Vehicle List; mandatory Search Results Title drilldown; Features scrape (cubic, vehicle_type ALL CAPS); dealer → Serial → Pre-check → PDI
- **Multi-line attach:** `order_line_vehicles` — per-line VIN, discount, ex-showroom scrape
- **Two-wheeler defaults:** seating=2, body_type=Open, num_cylinders=1

---

## 7. Database actions & reporting

### 7.1 Commit (after Invoice#)

Order: `customer_master` upsert → `vehicle_master` upsert → `sales_master` INSERT (fail on duplicate pair) → trigger `service_reminders_queue` if enabled.

| Path | Service |
|------|---------|
| Staging | `add_sales_commit_service.commit_staging_masters_and_finalize_row` |
| Sidecar | `POST /sidecar/dms/commit` |
| Video/legacy | `insert_dms_masters_from_siebel_scrape` |

Scraped columns on `sales_master`: `enquiry_number`, `order_number`, `invoice_number` (different DMS stages). **Not** DMS: `vahan_application_id`, `rto_charges`.

### 7.2 Run Report PDFs

- Default: **GST Retail Invoice**, **GST Booking Receipt**
- Path: `ocr_output/<dealer>/<subfolder>/{mobile}_{Report_Name}.pdf`
- API field: `hero_dms_form22_print`

### 7.3 Sidecar DMS endpoints

| Endpoint | Purpose |
|----------|---------|
| `/sidecar/dms/resolve` | Build fill values from staging |
| `/sidecar/dms/vehicle-after-prepare` | Checkpoint after `prepare_vehicle` |
| `/sidecar/dms/customer-after-prepare` | Checkpoint after `prepare_customer` |
| `/sidecar/dms/commit` | Finalize masters after invoice |

---

## 8. Admin: Cancel Invoice

`POST /admin/staging/{id}/cancel-invoice` — rolls back masters, resets staging for re-run (**Admin BRD**).

---

## 9. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Jun 2026 | Sidecar path, dms_state checkpoints, cloud vs Electron, admin cancel invoice |
| 1.0 | Jun 2026 | Initial domain split |
