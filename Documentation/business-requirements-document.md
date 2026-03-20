# Business Requirements Document (BRD)
## Auto Dealer Management System — Arya Agencies

**Version:** 0.8  
**Last Updated:** March 2026  
**Status:** Draft

---

## 1. Executive Summary

The system is a server–client application for auto dealers. Dealers run a lightweight client on their local machines; the server runs on AWS and handles data, document processing (OCR), and browser automation to push information to external portals (OEM DMS, Vahan/RTO, lenders).

---

## 2. Business Objectives

- **Centralize dealer data** (vehicles, customers, sales) in a single database.
- **Reduce manual data entry** by extracting text from uploaded documents (Tesseract OCR, AWS Textract, Vision API).
- **Automate portal submissions** by filling external web forms from database data (Playwright).
- **Streamline RTO workflow** — Submit Info, Fill DMS, Form 20 generation, Vahan submission, RTO payment tracking.
- **Keep the client lightweight** so it runs on typical dealer workstations without heavy infrastructure.

---

## 3. Stakeholders

| Role | Description |
|------|-------------|
| Dealer users | Staff at Arya Agencies (and future dealers) using the client app. |
| System / DevOps | Team managing AWS, database, and deployments. |

---

## 4. Business Rules and Constraints

| ID | Rule | Description |
|----|------|-------------|
| BR-1 | Aadhar storage | Only last 4 digits of Aadhar stored in DB; full number shown on frontend only (compliance). |
| BR-2 | Customer identification | Customer uniquely identified by (aadhar last 4, phone). |
| BR-3 | Date format | Default date format for app and DB is **dd/mm/yyyy** (e.g. 30/05/1980). |
| BR-4 | Sales uniqueness | One sale per (customer_id, vehicle_id); sales_id is auto-generated PK. |
| BR-5 | RTO application | One RTO application per sale (sales_id); application_id from Vahan is PK. |
| BR-6 | Service reminders | When dealer has auto_sms_reminders = Y, sales_master upsert triggers population of service_reminders_queue from oem_service_schedule. |
| BR-7 | Form 20 data | Form 20 fields populated from customer_master, vehicle_master, dealer_ref; vehicle data merged from DB when vehicle_id provided. |
| BR-8 | Bulk failure visibility | Bulk Upload `Error` and `Rejected` records must remain in the hot dashboard until the operator marks `action_taken=true`; older rows are not archived. |
| BR-9 | Automation source of truth | DMS and Vahan Playwright fills must read site field values from `form_dms_view` and `form_vahan_view` only. |
| BR-10 | Automation trace output | DMS and Vahan automation must write `DMS_Form_Values.txt` and `Vahan_Form_Values.txt` into the matching `ocr_output/<dealer>/<subfolder>/` folder. |
| BR-11 | Admin data reset preservation | Admin-triggered data reset must preserve `oem_ref`, `dealer_ref`, and `oem_service_schedule` while clearing all other public base tables. |
| BR-12 | Operator-assisted browser session requirement | DMS and Vahan automation should first reuse already open, logged-in site tabs; when no detectable tab is available, the system should open Edge/Chrome for the operator and ask the operator to complete login (first-time) before retrying automation. |
| BR-13 | Automation value discipline | Playwright must never assume, infer, remember, or default fill values for DMS/Vahan fields. Values sent to site fields must come directly from DB-backed views (`form_dms_view`, `form_vahan_view`) and persisted records only. |

---

## 5. Functional Requirements

### 5.1 Client Application (Dealer Workstation)

- **FR-1** Display dealership branding (e.g. "Arya Agencies") and current date.
- **FR-2** Allow users to view and manage dealer/location data (e.g. list, add dealers).
- **FR-3** Support upload of documents (e.g. buyer's order, forms) for OCR processing.
- **FR-4** Display status of jobs (e.g. OCR in progress, completed, failed).
- **FR-5** Allow users to review and correct OCR-extracted data before it is used.
- **FR-6** Trigger "send to portal" actions that enqueue automation jobs.
- **FR-7** Basic client-side validation (required fields, formats) with no heavy business logic.
- **FR-8** Add Sales flow: Submit Info (customer, vehicle, insurance) → Fill Forms (DMS, Form 20, RTO Queue insertion) → RTO Queue.
- **FR-9** RTO Queue page: list queued RTO work items created by Fill Forms and let an operator process the oldest 7 queued rows for their dealer in one browser session.
- **FR-10** View Customer page: search by mobile/plate, view vehicles and insurance, and inspect the selected vehicle's Vahan field mapping in a single horizontally scrollable row.
- **FR-10a** Main page: show an `Admin Saathi` tile with a destructive-action button that lets an operator clear all non-reference-table data after explicit confirmation.

### 5.2 Server / Backend

- **FR-11** Expose REST APIs for dealers, vehicles, customers, sales, documents, and the RTO queue.
- **FR-12** Accept document uploads, store files (local or S3), and create OCR jobs.
- **FR-13** Process OCR jobs (Tesseract), parse results, and persist structured data.
- **FR-14** Accept automation requests, enqueue them through SQS or the local in-process fallback queue, and track status.
- **FR-15** Run Playwright workers that log into external portals and submit data from database-backed views and records.
- **FR-16** Persist all business data in PostgreSQL with clear ownership (e.g. dealer_id) for multi-tenant use.
- **FR-17** Submit Info: upsert customer_master, vehicle_master, sales_master, insurance_master from extracted/entered data.
- **FR-18** Fill DMS: Playwright login to OEM DMS, read fill values only from `form_dms_view`, search vehicle, scrape data, download Form 21/22, write `DMS_Form_Values.txt` into the matching `ocr_output` subfolder, and update vehicle_master from scraped data.
- **FR-18a** Existing tab reuse with operator fallback: DMS/Vahan steps first reuse already open logged-in tabs; if none are detectable, API opens Edge/Chrome to the target site and returns a user-facing message asking the operator to login (first-time) and retry.
- **FR-19** Form 20: Generate Form 20.pdf from Word template (or PDF overlay / HTML fallback); fill placeholders from customer, vehicle, dealer; output combined PDF (front, back, page 3).
- **FR-19a** Gate Pass: Generate Gate Pass.pdf from Word template; fill placeholders (OEM name, today date, customer name, Aadhar, model, colour, key no., chassis no.); save to upload subfolder.
- **FR-20** RTO Queueing: after Fill Forms completes DMS/Form 20 work, create an `rto_queue` row with the dealer/customer/vehicle reference, estimated RTO fees, and queued status instead of auto-running the dummy Vahan site.
- **FR-20a** View-backed automation inspection: operators can inspect `form_vahan_view` from View Customer and review DMS/Vahan form-value exports under `ocr_output`.
- **FR-21** RTO status updates: queued rows remain visible in `rto_queue`, and downstream/manual RTO processing can update reference and payment fields later as needed.
- **FR-21a** Dealer batch processing: for each dealer, allow only one active Vahan browser session at a time while different dealers can run their own sessions independently.
- **FR-21b** RTO progress feedback: while a dealer batch is running, the RTO Queue page shows processed count, count added to RTO Cart, current queue row, and the latest error without requiring payment.
- **FR-21d** RTO failed-row retry: operators can click `Try Again` on a `Failed` RTO queue row to return it to `Queued` and run batch processing again.
- **FR-21c** RTO scrape persistence: when Vahan reaches the cart/upload checkpoint, the scraped application id and RTO charges must be stored on both `rto_queue` and `vehicle_master`, and later retries may overwrite those values with the newest scrape.
- **FR-22** Customer search: Search by mobile or plate; return customers with vehicles and insurance.
- **FR-23** QR decode: Decode Aadhar QR to extract customer details.
- **FR-24** Vision / Textract: Optional AI extraction from documents.

### 5.3 Non-Functional Requirements

- **NFR-1** Client: lightweight; minimal logic beyond validation and API calls.
- **NFR-2** Server: deployable on AWS; scalable for multiple dealers and job volume.
- **NFR-3** Data: stored in PostgreSQL; files in object storage or local uploads.
- **NFR-4** Security: authentication and authorization; dealer data isolated by tenant.

---

## 6. Form Navigation and Required Field Entry

This section defines the required operator navigation path and minimum field-entry contract for current forms.

### 6.1 DMS Navigation Sequence

1. Login (`index.html`)
2. Enquiry (`enquiry.html`)
3. Vehicle Search (`vehicle.html`)
4. Reports (`reports.html`)

### 6.2 DMS Fields to Fill (Minimum Contract)

| Page | Field label | Required source |
|------|-------------|-----------------|
| Enquiry | Contact First Name | `form_dms_view` (customer identity derived from DB records) |
| Enquiry | Contact Last Name | `form_dms_view` |
| Enquiry | Mobile Phone # | `form_dms_view` |
| Enquiry | State | `form_dms_view` |
| Enquiry | Address Line 1 | `form_dms_view` |
| Enquiry | Pin Code | `form_dms_view` |
| Vehicle Search | Key num (partial) | `form_dms_view` |
| Vehicle Search | Frame / Chassis num (partial) | `form_dms_view` |
| Vehicle Search | Engine num (partial) | `form_dms_view` |

### 6.3 Vahan Fields to Fill (Minimum Contract)

- All Vahan fields must be read from `form_vahan_view` labels and DB-backed technical columns (no hardcoded assumptions during runtime).
- Operator workflow remains: logged-in tab reuse first, fallback to auto-open browser and first-time login prompt, then retry.

### 6.4 Insurance Fields to Fill (Submit Info Contract)

- Insurance data captured in Submit Info must map to persisted DB columns before any downstream automation:
  - `insurance_master`: insurer, policy number, policy dates, premium, nominee fields.
  - `customer_master.profession`: profession captured with insurance context in Add Sales.

### 6.5 Download/Save Outputs

- DMS Reports must save:
  - `form21.pdf`
  - `form22.pdf`
  - `invoice_details.pdf`
- Automation trace files must save:
  - `ocr_output/<dealer>/<subfolder>/DMS_Form_Values.txt`
  - `ocr_output/<dealer>/<subfolder>/Vahan_Form_Values.txt`

### 6.6 Pending Video Confirmation

- The exact click-level sequence and any optional/extra operator entries must be confirmed from the DMS operator video and then reconciled with Playwright steps.

---

## 7. Bulk Upload Feature

Bulk upload automates the ingestion of scanned documents from a shared folder into the Add Customer flow.

### 6.1 Flow Overview

1. **Input Scans folder** — The only input folder for bulk processing. Scanned PDFs are placed in `Bulk Upload/<dealer_id>/Input Scans/` by the operator or an external scanner.
2. **Queue + ingest** — The ingest loop creates a hot `bulk_loads` row, moves the file into the queued working area, and publishes a job through the configured queue provider (`sqs` or local fallback).
3. **Pre-OCR** — The worker runs pre-OCR to extract a mobile number and supporting artifacts. These artifacts are written into the dealer's `ocr_output/<subfolder>/` folder.
4. **Add Customer processing** — If a mobile is found, the worker runs the Add Sales flow (Submit Info, DMS, Form 20, RTO queue insertion, terminal placement) and updates `bulk_loads` lifecycle columns.
5. **Terminal routing** — On success, files move to `Success/{mobile_ddmmyyyy}/`; on validation or processing failures, they move to `Error/{filename_ddmmyyyy}/` or `Rejected scans/` as applicable.
6. **Re-process on error** — When a bulk load fails, the operator can use **Re-process** on the Bulk Loads page. This opens Add Sales with the mobile and associated files pre-filled so the operator can correct data and complete the flow manually.

### 6.2 Key Behaviours

- One worker lease processes a job at a time; recovery uses `leased_until`, `attempt_count`, and `worker_id`.
- While a bulk job is waiting in SQS/local queue order, the hot row shows `status='Queued'`; once a worker leases it, the row changes to `Processing`.
- Bulk ingest/worker can run inside the API process or through the standalone `run_bulk_worker.py` process.
- The Bulk Loads table shows current status, queue stage, source/result folders, and operator-visible error details from the hot table.
- Re-process uses the stored mobile and Error-folder files to pre-populate Add Sales.
- The Bulk Loads UI and APIs read from the hot `bulk_loads` table only.
- Older bulk rows are not archived; retention remains entirely in `bulk_loads`.

---

## 8. Out of Scope (Current Phase)

- Mobile apps.
- Real-time collaboration.
- Full OEM/DMV/lender portal coverage (to be expanded incrementally).
- JWT/OAuth authentication (planned).

---

## 9. Success Criteria

- Dealer can add/view dealer records via the client against the live backend.
- Document upload creates an OCR job and extracted data is stored and reviewable.
- Submit Info persists customer, vehicle, sales, insurance.
- Fill DMS scrapes vehicle data, downloads Form 21/22, and writes `DMS_Form_Values.txt` under `ocr_output`.
- Form 20.pdf is generated and saved to upload subfolder.
- Fill Forms inserts an `rto_queue` row instead of auto-submitting the dummy Vahan site.
- RTO Queue page lists the queued RTO work items for follow-up processing and can process the oldest 7 rows in one dealer-scoped browser session.
- The operator can see how many rows were added to the RTO Cart before any payment step.
- Bulk upload processing can ingest, queue, process, and retain the required hot-table status history in `bulk_loads`.
- Documentation (HLD, LLD, Technical Architecture, Database DDL) is maintained under the project.

---

## 10. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial BRD for Auto Dealer system |
| 0.2 | Mar 2025 | — | Added business rules (BR-1 to BR-7); FR-8 to FR-24 (Submit Info, Fill DMS, Form 20, Vahan, RTO payment, customer search, QR decode) |
| 0.3 | Mar 2025 | — | Added Section 6: Bulk Upload Feature (Scan folder, pre-OCR, Add Customer processing, Re-process on error) |
| 0.4 | Mar 2026 | — | Bulk rows stay in the hot table; unresolved Error/Rejected rows remain visible until action taken |
| 0.5 | Mar 2026 | — | Expanded View Customer to show the selected vehicle's Vahan field mapping row |
| 0.6 | Mar 2026 | — | Added DMS field logging requirement via `DMS_Form_Values.txt` and supporting DB view |
| 0.7 | Mar 2026 | — | DMS/Vahan automation now reads site field values only from `form_dms_view` and `form_vahan_view` |
| 0.8 | Mar 2026 | — | Updated bulk upload behavior, queue model, operator rules, and automation trace outputs to match the current implementation |
| 0.9 | Mar 2026 | — | Added Admin Saathi reset requirement and preserved-table rule |
| 1.0 | Mar 2026 | — | Updated automation behavior to reuse already open DMS/Vahan tabs and fail with site-not-open errors when tabs are unavailable |
| 1.1 | Mar 2026 | — | Added operator fallback: if no detectable DMS/Vahan tab exists, backend opens Edge/Chrome and prompts first-time login before retry |
| 1.2 | Mar 2026 | — | Added form navigation + minimum field-entry contracts for DMS/Vahan/Insurance and formalized no-assumption Playwright value discipline |
