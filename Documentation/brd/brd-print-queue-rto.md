# BRD — Print / Queue RTO

**Version:** 2.0  
**Last Updated:** June 2026  
**Parent:** [README.md](README.md)

Form 20 and Gate Pass generation, Electron print pipeline, and RTO queue row creation before Vahan batch processing.

---

## 1. Business rules

| ID | Rule |
|----|------|
| PRT-BR-1 | Form 20 from customer, vehicle, dealer masters (**BR-7**) |
| PRT-BR-2 | Insert `rto_queue` after print step — do not auto-run Vahan (**FR-20**) |
| PRT-BR-3 | One queue row per sale (**BR-5**) |
| PRT-BR-4 | Gate Pass: OEM, today, customer, Aadhar last 4, model, colour, key, chassis |
| PRT-BR-5 | Electron print pipeline required for full operator experience |
| PRT-BR-6 | Failures logged to `process_failure_log` |

---

## 2. PDF generation

| Document | API (cloud) | Electron |
|----------|-------------|----------|
| Form 20 | `POST /fill-forms/print-form20` | Included in sale folder on server |
| Gate Pass | `POST /fill-forms/print-gate-pass` | Sidecar `print_gate_pass_local` (template from `/sidecar/gate-pass-context`) |
| Status | `GET /fill-forms/form20-status` | Template diagnostics |

Templates: Word DOCX → PDF (LibreOffice/docx2pdf fallback).

---

## 3. Print / Queue RTO pipeline (Electron)

Operator clicks **Print Forms and Queue RTO** after insurance step:

1. **Pull** sale scans from server → local PC
2. **Overlay** dealer signatures on invoice/GST PDFs
3. **Gate Pass** — generate + silent print
4. **Push** sale folder ZIP to server (`/sidecar/push-sale-bundle`)
5. **Insert** RTO queue row — `POST /rto-queue`
6. **Log** print/RTO trace; record failure if any step fails

Gating: **New** sale blocked until this step completes for current staging row.

---

## 4. RTO queue insertion

| Field | Source |
|-------|--------|
| dealer_id, customer_id, vehicle_id | From committed sale |
| sales_id | FK |
| staging_id | Optional link to `add_sales_staging` |
| status | **Queued** (initial) |
| in_queue | **true** (default — eligible for Vahan batch) |
| estimated fees | From dealer/RTO config at insert |

Does **not** scrape Vahan application id — that happens in **Vahan BRD**.

---

## 5. Service reminders

On `sales_master` INSERT/UPDATE when `dealer_ref.auto_sms_reminders = Y`: trigger maintains `service_reminders_queue` (**BR-6**). No separate Service Saathi UI yet.

---

## 6. Related APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/rto-queue` | Create/update queue row |
| GET | `/rto-queue/by-sale` | Lookup by sale |
| POST | `/sidecar/push-sale-bundle` | ZIP upload after local print |
| POST | `/sidecar/sync-sale-folder-s3` | Re-sync folder to object storage |
| POST | `/sidecar/failure-log` | Terminal failure upsert |

---

## 7. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Jun 2026 | Electron print pipeline, gate pass local, push-sale-bundle, failure log |
| 1.0 | Jun 2026 | Initial domain split |
