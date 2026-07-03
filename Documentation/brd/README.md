# Business Requirements — Index

**Product:** Dealer Saathi (Electron desktop + FastAPI backend)  
**Version:** 4.1  
**Last Updated:** June 2026  
**Status:** Current (codebase sync)

---

## 1. Purpose

Business requirements are organized by product domain. Each domain BRD is maintained against the live repo (`backend/app`, `client/src`, `electron/sidecar`).

**Architecture split:**

| Runtime | Role |
|---------|------|
| **Electron + sidecar** | Primary dealer deployment — local Playwright (DMS, insurance, Vahan, challan, print) |
| **FastAPI (cloud/local)** | REST API, OCR, staging DB, sidecar DB proxy (`/sidecar/*`) |
| **Browser-only dev** | Same UI; automation falls back to cloud `/fill-forms/*` where sidecar unavailable |

---

## 2. Cross-cutting business rules

| ID | Rule | Applies to |
|----|------|------------|
| BR-1 | Aadhar: last 4 digits in DB only | All customer-facing domains |
| BR-2 | Customer key: (aadhar last 4, mobile) | Dealer Saathi, DMS |
| BR-3 | Date format **dd/mm/yyyy** | All |
| BR-4 | One sale per (customer_id, vehicle_id) | Dealer Saathi, DMS |
| BR-5 | One RTO queue row per sale | Print/RTO, Vahan |
| BR-6 | `service_reminders_queue` — DB trigger only; app must not write | Sales commit |
| BR-7 | Form 20 / Gate Pass from masters + dealer_ref | Print/RTO |
| BR-8 | Bulk Error/Rejected visible until `action_taken=true` | Dealer Saathi |
| BR-9 | DMS: staging JSON + scrape; Vahan: `form_vahan_view`; Insurance: view + staging; CPA: `form_cpa_insurance_view` + staging | Automation |
| BR-10 | Traces under `ocr_output/<dealer>/<subfolder>/` | DMS, Insurance, Vahan |
| BR-11 | Admin reset preserves `*ref` tables + `oem_service_schedule` | Admin |
| BR-12 | Reuse logged-in tabs; else open browser + operator login | DMS, Insurance, Vahan |
| BR-13 | No invented/default fill values in automation | All Playwright |
| BR-18 | Address inference (`C/O`, `DIST:`, state–PIN) | OCR, DMS |
| BR-23 | Bulk `raw/` PDF-only (`page_NN.pdf`, OSD rotation) | Bulk |
| BR-24 | Tesseract OSD orientation when confidence sufficient | OCR, bulk |
| BR-25 | JWT auth on API; `GET /auth/me` drives home tiles and dealer scope | All client modes |
| BR-26 | Electron sidecar jobs call `/sidecar/*` — no direct DB from desktop Playwright | DMS, Insurance, Vahan, challan, print |

---

## 3. Domain BRDs

| # | Document | Scope |
|---|----------|--------|
| 1 | [BRD — DMS](brd-dms.md) | Siebel Create Invoice; sidecar checkpoints; Run Report PDFs |
| 2 | [BRD — Subdealer Challans](brd-subdealer-challans.md) | DDR OCR, staging, local/server batch, committed invoices |
| 3 | [BRD — Insurance and CPA](brd-insurance-and-cpa.md) | Hero MISP GI, CPA Alliance, `insurance_type` Main/CPA |
| 4 | [BRD — Print / Queue RTO](brd-print-queue-rto.md) | Form 20, Gate Pass, print pipeline, RTO queue insert |
| 5 | [BRD — Vahan](brd-vahan.md) | Workbench batch, OTP, forms upload, queue lifecycle |
| 6 | [BRD — Admin Saathi](brd-admin-saathi.md) | Dealers, logins, staging ops, usage/failure/OCR logs |
| 7 | [BRD — Dealer Saathi](brd-dealer-saathi.md) | Add Sales (New / In-process / Invoices), auth, bulk, search |

---

## 4. Client modes (Home tiles)

| Mode | Tile flag | Primary pages |
|------|-----------|---------------|
| **POS (Sales Window)** | `tile_pos` | Add Sales, Subdealer Challans*, Bulk Loads†, View Customers, View Vehicles |
| **RTO Desk** | `tile_rto` | RTO Queue |
| **Dealer Saathi** | `tile_dealer` | Dashboard, Sales Reports, View Customers, View Vehicles |
| **Admin Saathi** | admin role | Dealers, Usage, Admin Tools |
| **Service Saathi** | `tile_service` | Placeholder (Service Reminders) |

\* Principal dealers only (`parent_id` null).  
† Login `shashank` only (bulk loads tab).

---

## 5. Stakeholders

| Role | Description |
|------|-------------|
| Dealer users | Staff using Electron client at dealership |
| Admin users | Scoped via `admin_dealer_access_ref` |
| System / DevOps | AWS RDS, S3, deployments |

---

## 6. Out of scope (current phase)

- Native mobile apps
- Service Reminders operator UI (DB trigger exists)
- Full non-Hero OEM DMS without explicit extension

---

## 7. Document control

| Version | Date | Changes |
|---------|------|---------|
| 4.1 | Jun 2026 | Full codebase refresh: auth, sidecar, Add Sales tabs, RTO lifecycle, admin, CPA `insurance_type` |
| 4.0 | Jun 2026 | Initial split from monolithic BRD v3.6 |
