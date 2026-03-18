# Business Requirements Document (BRD)
## Auto Dealer Management System — Arya Agencies

**Version:** 0.3  
**Last Updated:** March 2025  
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
| BR-8 | Bulk failure visibility | Bulk Upload `Error` and `Rejected` records must remain in the hot dashboard until the operator marks `action_taken=true`; only resolved failures may be archived. |

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
- **FR-8** Add Sales flow: Submit Info (customer, vehicle, insurance) → Fill Forms (DMS, Vahan, Form 20) → RTO Payments Pending.
- **FR-9** RTO Payments Pending page: list pending RTO applications, record payment (txn_id, payment_date).
- **FR-10** View Customer page: search by mobile/plate, view vehicles and insurance.

### 5.2 Server / Backend

- **FR-11** Expose REST APIs for dealers, vehicles, customers, sales, documents, RTO payments.
- **FR-12** Accept document uploads, store files (local or S3), and create OCR jobs.
- **FR-13** Process OCR jobs (Tesseract), parse results, and persist structured data.
- **FR-14** Accept automation requests, enqueue them (Redis or SQS), and track status.
- **FR-15** Run Playwright workers that log into external portals and submit data from the database.
- **FR-16** Persist all business data in PostgreSQL with clear ownership (e.g. dealer_id) for multi-tenant use.
- **FR-17** Submit Info: upsert customer_master, vehicle_master, sales_master, insurance_master from extracted/entered data.
- **FR-18** Fill DMS: Playwright login to OEM DMS, search vehicle, scrape data, download Form 21/22; update vehicle_master from scraped data.
- **FR-19** Form 20: Generate Form 20.pdf from Word template (or PDF overlay / HTML fallback); fill placeholders from customer, vehicle, dealer; output combined PDF (front, back, page 3).
- **FR-19a** Gate Pass: Generate Gate Pass.pdf from Word template; fill placeholders (OEM name, today date, customer name, Aadhar, model, colour, key no., chassis no.); save to upload subfolder.
- **FR-20** Vahan (RTO): Playwright fill Vahan portal; create rto_payment_details row with application_id, rto_fees, status Pending.
- **FR-21** RTO payment: Update rto_payment_details to Paid with pay_txn_id, payment_date.
- **FR-22** Customer search: Search by mobile or plate; return customers with vehicles and insurance.
- **FR-23** QR decode: Decode Aadhar QR to extract customer details.
- **FR-24** Vision / Textract: Optional AI extraction from documents.

### 5.3 Non-Functional Requirements

- **NFR-1** Client: lightweight; minimal logic beyond validation and API calls.
- **NFR-2** Server: deployable on AWS; scalable for multiple dealers and job volume.
- **NFR-3** Data: stored in PostgreSQL; files in object storage or local uploads.
- **NFR-4** Security: authentication and authorization; dealer data isolated by tenant.

---

## 6. Bulk Upload Feature

Bulk upload automates the ingestion of scanned documents from a shared folder into the Add Customer flow.

### 6.1 Flow Overview

1. **Input Scans folder** — The only input folder for bulk processing. Scanned PDFs (e.g. `Scan1.pdf` or `Scans.pdf` in subfolders) are placed in `Bulk Upload/Input Scans/` by the operator or an external scanner. The watcher monitors this folder only.
2. **Pre-OCR** — A background watcher picks up new scans, copies them to a Processing folder, and runs pre-OCR (Tesseract or AWS Textract) to extract the mobile number from the document. The mobile is required for Add Customer.
3. **Add Customer processing** — If a mobile is found, the system invokes the Add Customer flow (Submit Info, Fill DMS, Form 20, Vahan, etc.) and records the result in the bulk_loads table. On success, files are moved to `Success/{mobile_ddmmyyyy}/`; on error, to `Error/{filename_ddmmyyyy}/`.
4. **Re-process on error** — When a bulk load fails (e.g. pre-OCR fails, mobile not found, or Add Customer fails), the operator can use the **Re-process** button on the Bulk Loads page. This opens the Add Customer screen with the mobile and associated files pre-filled so the operator can correct data and complete the flow manually.

### 6.2 Key Behaviours

- One scan is processed at a time; the watcher polls periodically.
- Pre-OCR output is saved as `{filename}_ddmmyyyy_pre_ocr.txt` in the Processing folder.
- The Bulk Loads table shows status (Processing, Success, Error), source filename, folder link, and error details.
- Re-process uses the stored mobile and Error-folder files to pre-populate Add Customer.
- The Bulk Loads UI and APIs read from the hot `bulk_loads` table only. Archived history is backend-only until an archive read path is added.
- Archival keeps `Success` rows eligible after retention, but `Error` and `Rejected` rows remain in hot storage until the operator marks them corrected.

---

## 7. Out of Scope (Current Phase)

- Mobile apps.
- Real-time collaboration.
- Full OEM/DMV/lender portal coverage (to be expanded incrementally).
- JWT/OAuth authentication (planned).

---

## 8. Success Criteria

- Dealer can add/view dealer records via the client against the live backend.
- Document upload creates an OCR job and extracted data is stored and reviewable.
- Submit Info persists customer, vehicle, sales, insurance.
- Fill DMS scrapes vehicle data and downloads Form 21/22.
- Form 20.pdf is generated and saved to upload subfolder.
- Vahan submission creates RTO application; payment can be recorded.
- RTO Payments Pending page lists and updates payment status.
- Documentation (HLD, LLD, Technical Architecture, Database DDL) is maintained under the project.

---

## 9. Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | Mar 2025 | — | Initial BRD for Auto Dealer system |
| 0.2 | Mar 2025 | — | Added business rules (BR-1 to BR-7); FR-8 to FR-24 (Submit Info, Fill DMS, Form 20, Vahan, RTO payment, customer search, QR decode) |
| 0.3 | Mar 2025 | — | Added Section 6: Bulk Upload Feature (Scan folder, pre-OCR, Add Customer processing, Re-process on error) |
| 0.4 | Mar 2026 | — | Added bulk archival rule: unresolved Error/Rejected rows stay hot until action taken; archive remains backend-only for now |
