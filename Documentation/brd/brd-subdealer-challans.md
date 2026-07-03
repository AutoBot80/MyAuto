# BRD — Subdealer Challans

**Version:** 2.0  
**Last Updated:** June 2026  
**Parent:** [README.md](README.md)

POS Saathi — stock transfer from **from_dealer** to **to_dealer** via Daily Delivery Report OCR, staging, DMS batch, committed challan masters.

---

## 1. Business rules (**BR-22**)

| Rule | Detail |
|------|--------|
| Separate automation | Not retail Find Contact Enquiry / `add_sales_staging` |
| Batch UUID | One `challan_batch_id` per Create Challans action |
| Vehicle prep | `prepare_vehicle` per Queued line — no retail contact sweep |
| Inventory | Upsert `vehicle_inventory_master` for **to_dealer_id** |
| Discount | `subdealer_discount_master_ref`: from_dealer + to_dealer's `subdealer_type` + model prefix (longest match); else **1500.00** |
| Transport | Optional `add_transport_cost` + `transport_cost_per_vehicle` per line |
| Reduce discount % | Optional `reduce_discount_by_percent` when transport flag set: `base − (base × pct/100) − cost_per_vehicle` (may go negative) |
| Siebel identity | Mobile `0000000000`; Network customer from to_dealer; Comments `From {from}. Helmet credited` |
| Commit | `challan_master` + `challan_details` with transport/discount snapshots |

---

## 2. Client workflow

| Step | Action |
|------|--------|
| Upload | Multi-select PDF/JPEG/PNG/WebP |
| Parse | One `POST /subdealer-challan/parse-scan` per file |
| Merge | Client: max book number, lines in file order, de-dupe engine+chassis |
| New Challan | Select to_dealer; optional transport + reduce % + cost per vehicle |
| Create | `POST /subdealer-challan/staging` → `process/{batch_id}` |
| Processed tab | Recent batches, failed lines, retries |

**Electron:** sidecar `fill_subdealer_challan`; local OCR artifact mirror.

**Visibility:** POS tab for principal dealers only (`parent_id` null).

---

## 3. APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/subdealer-challan/parse-scan` | Textract DDR parse |
| POST | `/subdealer-challan/staging` | Header + detail lines |
| POST | `/subdealer-challan/process/{batch_id}` | Full batch (API host, long-running) |
| GET | `/subdealer-challan/staging/recent` | Processed tab |
| GET | `/subdealer-challan/staging/failed-count` | Nav badge |
| PATCH | `/subdealer-challan/staging/detail/{id}` | Correct raw chassis/engine |
| PATCH | `/subdealer-challan/staging/master/{batch_id}` | Change to_dealer |
| POST | `…/staging/{detail_id}/retry` | Full line retry |
| POST | `…/batch/{batch_id}/retry-order` | Order-only retry |
| GET | `/subdealer-challan/invoices/recent` | Committed challans |
| GET | `/subdealer-challan/invoices/{id}/details` | Line details |

---

## 4. Line statuses

**Queued** → **Ready** / **Failed** → **Committed**

Header `invoice_status`: Pending / Failed / Completed

---

## 5. Sidecar endpoints

| Endpoint | Purpose |
|----------|---------|
| `/sidecar/subdealer-challan/resolve` | Batch payload for local Playwright |
| `/sidecar/subdealer-challan/prepare-result` | Per-line prepare outcome |
| `/sidecar/subdealer-challan/order-context` | Discounts + order lines |
| `/sidecar/subdealer-challan/order-checkpoint` | Order#/VIN attach progress |
| `/sidecar/subdealer-challan/finalize-order` | Invoice + challan_master |
| `/sidecar/subdealer-challan/requeue-failed` | Reset Failed → Queued |

---

## 6. Dealer dashboard integration

Challan widgets on **Dealer Dashboard**: matrices, filtered lists, IST-day drill-down (`/dealers/{id}/dashboard/challans-*`).

---

## 7. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Jun 2026 | reduce_discount_by_percent, PATCH staging, invoices APIs, sidecar path |
| 1.0 | Jun 2026 | Initial domain split |
