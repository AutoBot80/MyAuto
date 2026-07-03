# BRD — Vahan

**Version:** 2.0  
**Last Updated:** June 2026  
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

## 7. APIs (summary)

| Method | Path |
|--------|------|
| GET | `/rto-queue?dealer_id=` |
| POST | `/rto-queue/process-batch` |
| GET | `/rto-queue/process-batch/status` |
| POST | `/fill-forms/vahan/warm-browser` |
| GET | `/customer-search/form-vahan` |

Legacy `POST /fill-forms/vahan` fill — **not implemented**; use process-batch.

---

## 8. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Jun 2026 | Full status model, OTP/mobile, forms missing, in_queue, sidecar batch, requeue/mark-done |
| 1.0 | Jun 2026 | Initial domain split |
