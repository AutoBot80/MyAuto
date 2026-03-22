# Business Requirements Document (BRD)
## Auto Dealer Management System — Arya Agencies

**Version:** 2.4  
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
| BR-14 | DMS booking test budget | Dummy / training DMS flow uses a fixed **customer budget / enquiry amount of 89000** for booking generation unless the business replaces this constant in automation. |
| BR-15 | Ex-showroom vs column name | **Order Value / ex-showroom** from DMS is persisted as `vehicle_master.vehicle_price` (no separate `vehicle_cost` column). Labels in exports and dummy UI read **Ex-showroom Price**. |
| BR-16 | Create Invoice operator-only | DMS Playwright must not click **Create Invoice**; the operator completes invoicing, then may re-run automation after the invoice step. |
| BR-17 | Aadhaar gender default | When **gender** is not extracted from UIDAI QR, Tesseract, or Textract for a subfolder that includes `Aadhar.jpg` and/or `Aadhar_back.jpg`, the persisted `customer.gender` in `OCR_To_be_Used.json` defaults to **Male** (operators may correct before submit). |
| BR-18 | Address-derived locality | When **state**, **PIN**, or **care_of** is missing but **address** contains **`C/O:`** (Care of), **`DIST: <District>, <State> - <PIN>`** (including OCR variants like **`<State> - - <PIN>`** or **`<State> -- <PIN>`**), a trailing **`<Indian state> - <PIN>`** when **`DIST:`** is unreadable, and/or a 6-digit PIN, the system infers **`care_of`**, **`city`/district**, **`state`**, **`pin`**; drops text **after the PIN**; strips **C/O** from the stored address line. Applied on Submit Info, OCR JSON, Aadhaar back parsing, and DMS fill when `form_dms_view` fields are sparse. |

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
- **FR-8** Add Sales flow: Submit Info (customer, vehicle, insurance) → operator-triggered actions with separate controls (`Fill DMS`, `Fill Insurance`, `Print Forms`) → RTO Queue insertion during print step.
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
- **FR-18** Fill DMS: Playwright reads fill values only from `form_dms_view`. **Target real-DMS step order** is **§6.1a**; details and residual gaps: **LLD §2.4d**. **`"DMS Contact Path"`** may be **`skip_find`**: dummy DMS skips the contact-finder Go step and proceeds to enquiry form + Generate booking and the rest; **real Siebel** **`skip_find`:** enquiry URL, full customer form + **Save**, then **`DMS_REAL_URL_VEHICLE`**; **Generate Booking** only **after** vehicle scrape when the row is **not** In Transit. **Real Siebel default path:** Find→Contact, mobile only, Go; existing match → care-of only + Save; no match or **`new_enquiry`** → full form + Save; vehicle scrape with **In Transit** detection; branch receipt → **Pre Check** → **PDI** vs booking/allotment per **§6.1a** (optional **`DMS_REAL_URL_PRECHECK`**; else Pre Check is attempted on **`DMS_REAL_URL_PDI`** before PDI submit). **`DMS_MODE=dummy`:** enquiry (contact find or `new_enquiry`) → stock/PDI → vehicle search → allocate → invoicing-line (no Create Invoice) → reports on static HTML under `DMS_BASE_URL`; scrape vehicle row (**ex-showroom** into `vehicle_price`); download Form 21/22 and GST invoice sheet PDF; write traces into `ocr_output`; update `vehicle_master` from scraped data. **`DMS_MODE=real`:** `siebel_dms_playwright.run_hero_siebel_dms_flow`; write `DMS_Form_Values.txt`; tune `DMS_SIEBEL_*` and `DMS_REAL_URL_*`; static training PDF downloads are not used.
- **FR-18a** Existing tab reuse with operator fallback: DMS/Vahan steps first reuse already open logged-in tabs; if none are detectable, API opens Edge/Chrome to the target site and returns a user-facing message asking the operator to login (first-time) and retry.
- **FR-18b** Insurance fill step: Playwright fills Insurance portal fields from persisted DB records only, reuses an already open logged-in insurance tab (or opens browser and asks operator to login first-time), does not click final issue/submit, and keeps the browser open for operator review.
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
- **FR-23** QR decode: Decode Aadhaar UIDAI QR to extract customer details; support QR on **front (`Aadhar.jpg`) and/or back (`Aadhar_back.jpg`)**, merging fields with front preferred when both are readable. When QR or local OCR misses fields, **AWS Textract text** from the same scans (also written to `Raw_OCR.txt`) is parsed as a fallback for **DOB and gender** on the front and **printed English address** on the back (e.g. “Near Gayatri School …”). **Gender** uses a **DOB anchor** (after `dd/mm/yyyy`, skip one token, next `/`, then gender), with fallbacks for **`Gender:`** / **Sex/** / **yes/** OCR. **State/PIN** use **comma-separated clauses** and **dash runs** before the last 6-digit PIN, plus **`DIST:`** and trailing-state patterns; back **`Address:`** blocks include the following **`C/O:`** line.
- **FR-24** Vision / Textract: Optional AI extraction from documents.

### 5.3 Non-Functional Requirements

- **NFR-1** Client: lightweight; minimal logic beyond validation and API calls.
- **NFR-2** Server: deployable on AWS; scalable for multiple dealers and job volume.
- **NFR-3** Data: stored in PostgreSQL; files in object storage or local uploads.
- **NFR-4** Security: authentication and authorization; dealer data isolated by tenant.

---

## 6. Form Navigation and Required Field Entry

This section defines the required operator navigation path and minimum field-entry contract for current forms.

### 6.1 DMS Navigation Sequence (dummy training HTML)

1. Login (`index.html`)
2. Enquiry (`enquiry.html`)
3. Vehicle Search (`vehicle.html`)
4. Reports (`reports.html`)

### 6.1a Hero Connect / Siebel eDealer — Target DMS automation sequence (ordered)

This is the **intended** real-DMS order (aligned with the operator screen recording and dealer workflow). Values on every fill step must come only from `form_dms_view` and persisted records (BR-9, BR-13). **Create Invoice** is never automated (BR-16). Playwright implementation parity is tracked in **LLD §2.4d**.

| Step | Siebel module / screen (typical labels) | Action |
|------|----------------------------------------|--------|
| 0 | **Hero Connect** (session) | Operator is signed in. Automation reuses a logged-in tab when possible (BR-12); otherwise opens browser and waits for operator login before continuing. |
| 1 | **Contacts** (or Buyer/CoBuyer contact view — `DMS_REAL_URL_CONTACT`) | If **Find** is collapsed, expand it; set **Find** object type to **Contact**; enter **Mobile**; run query (**Go** / **Find**). |
| 2a | **Contact / Enquiry — not found** | **New enquiry** (or **New** on enquiry list): enter full customer + enquiry fields required by the tenant from `form_dms_view`; **Save**. If the booking path applies (step 4b), continue to **Generate Booking** after the record is valid. |
| 2b | **Contact / Enquiry — found** | Select the correct row when **multiple matches** exist (business rule TBD: e.g. latest enquiry, exact name match). Update **S/O or W/o** and **Father / Husband / Care of** (and related care-of fields) from DB-backed values only; **Save**. Do not replace the whole customer record unless the product owner explicitly extends this rule. |
| 3 | **Vehicles → Auto Vehicle List** (`DMS_REAL_URL_VEHICLE`) | Query by **Key / VIN (chassis) / Engine** partials; execute find; open or read the row and determine **stock status** (e.g. **In Transit** vs available for booking/allocation). |
| 4a | **Branch: In Transit** | **Vehicles Receipt → HMCL – In Transit** (or tenant-equivalent): **Process Receipt** for the VIN. Then complete **Pre Check** / **PDI Pre-Check** (often on the same GotoView as PDI — `DMS_REAL_URL_PRECHECK` optional, else first actions on `DMS_REAL_URL_PDI`) **before** main **Auto Vehicle PDI Assessment** submit (`DMS_REAL_URL_PDI`): complete required **inspection** sub-forms; **Submit** / save as the UI requires. |
| 4b | **Branch: not In Transit (booking path)** | **Enquiry → My Enquiries** (or current enquiry): **Generate Booking** when creating/linking the sales order. **Vehicle Sales → Invoice** (order): **Allotment** tab — **Price All** / **Allocate** (and related line actions) as required. |
| 5 | **Tenant-dependent gates (if shown)** | Handle or stop for operator: **Vehicle Digitization** (e.g. OTP), **Document Upload**, **Sanction Details**, **Validate GL Voucher**, **WOT Details** (if exchange), **Contacts → Payments**, finance/hypothecation dialogs. No invented clicks — follow persisted flags and visible prompts. |
| 6 | **End of automation** | **Do not** click **Create Invoice**. Leave the **browser window open** for operator review (same session discipline as insurance automation). **Run Report** / GST PDF downloads are out of scope unless added under a separate FR. |

### 6.2 DMS Fields to Fill (Minimum Contract)

| Page | Field label | Required source |
|------|-------------|-----------------|
| Enquiry | Contact First Name | `form_dms_view` (customer identity derived from DB records) |
| Enquiry | Contact Last Name | `form_dms_view` |
| Enquiry | Mobile Phone # | `form_dms_view` |
| Enquiry | Landline # | `form_dms_view` (`customer_master.alt_phone_num`) |
| Enquiry | State | `form_dms_view` |
| Enquiry | Address Line 1 | `form_dms_view` |
| Enquiry | Pin Code | `form_dms_view` |
| Vehicle Search | Key num (partial) | `form_dms_view` |
| Vehicle Search | Frame / Chassis num (partial) | `form_dms_view` |
| Vehicle Search | Engine num (partial) | `form_dms_view` |
| Enquiry | Relation (S/O or W/o), Father/Husband name | `form_dms_view` — from `customer_master.care_of` (Aadhaar QR), else legacy `father_or_husband_name` |
| Enquiry | Financier / Finance Required (invoicing line) | `form_dms_view` (`customer_master.financier`) |
| Enquiry | New vs existing CRM contact | `form_dms_view` (`"DMS Contact Path"`: `found` / `new_enquiry` / **`skip_find`** = skip Find/mobile search; dummy → form + Generate booking; real → enquiry view, customer form + Generate Booking, then vehicles) |
| Invoicing line | Order Value (Ex-showroom) | Scraped from DMS vehicle grid → `vehicle_master.vehicle_price` |

### 6.3 Vahan Fields to Fill (Minimum Contract)

- All Vahan fields must be read from `form_vahan_view` labels and DB-backed technical columns (no hardcoded assumptions during runtime).
- Operator workflow remains: logged-in tab reuse first, fallback to auto-open browser and first-time login prompt, then retry.

### 6.4 Insurance Fields to Fill (Submit Info Contract)

- Insurance data captured in Submit Info must map to persisted DB columns before any downstream automation:
  - `insurance_master`: insurer, policy number, policy dates, premium, nominee fields.
  - `customer_master`: `profession`, `financier`, `marital_status`, `nominee_gender`, `care_of` (Aadhaar QR care-of / father–husband), `dms_relation_prefix`, `dms_contact_path` captured with details-sheet / DMS automation context in Add Sales (`father_or_husband_name` is legacy only).

### 6.5 Insurance Navigation Sequence (Video-Aligned)

1. Login page (`misp.heroinsurance.com` / dummy `index.html`): operator enters credentials and **Login**; Playwright waits (up to `INSURANCE_LOGIN_WAIT_MS`) until KYC is shown, then automates KYC.
2. KYC verification page (`ekycpage.aspx` / dummy `kyc.html`): enter mobile → **Verify mobile**; if KYC not on file, upload three documents (Aadhaar front, rear, customer photo) → consent → **Submit** to advance; dummy uses `#ins-check-mobile` / `#ins-kyc-submit` / `policy.html` flow.
3. KYC success auto-redirect screen
4. MisDMS policy entry page (`MispDms.aspx`) with VIN input
5. New policy creation page (`MispPolicy.aspx`) for "New Policy - Two Wheeler"
6. (Optional reference tab) Hero Connect lookup for invoice/vehicle context, then return to MisDMS policy flow

### 6.6 Insurance Labels to Minimum Data Source Contract

| Insurance page | Label | Required source (DB-backed) | Persisted DB column |
|----------------|-------|------------------------------|---------------------|
| KYC | Insurance Company | Details-sheet / policy **insurance provider** name (fuzzy-matched to portal options) | `insurance_master.insurer` |
| KYC | KYC Partner | Dealer-configured onboarding value | Reference/config (runtime choice, not persisted in current schema) |
| KYC | Proposer Type | Portal default | Dummy/default **Individual**; Playwright does not change tenure/proposer selects |
| KYC | OVD Type | Document type from scan set | derived from uploaded docs metadata |
| KYC | Mobile No. | Customer mobile | `customer_master.mobile_number` |
| KYC | AADHAAR Front/Rear Image | Uploaded scan artifacts | `uploads/<dealer>/<subfolder>/` files |
| KYC | Customer Photo | Uploaded scan artifacts | `uploads/<dealer>/<subfolder>/` files |
| MisDMS Entry | VIN Number | Vehicle chassis/frame | `vehicle_master.chassis` (or raw frame column) |
| New Policy | Insurance Company* | Same as KYC: fuzzy-match to details insurer | `insurance_master.insurer` |
| New Policy | Policy Tenure | Portal default | Dummy option only; Playwright does not set |
| New Policy | Manufacturer* | OEM / make (fuzzy-matched to portal options) | `vehicle_master.oem_name`, else dealer `oem_ref.oem_name` |
| New Policy | Proposer Type* | Portal default | Dummy **Individual**; Playwright does not set |
| New Policy | Proposer Name | Customer name | `customer_master.name` |
| New Policy | Gender | Customer gender | `customer_master.gender` |
| New Policy | Date of Birth | Customer DOB | `customer_master.date_of_birth` |
| New Policy | Marital Status | Customer marital status | `customer_master.marital_status` |
| New Policy | Occupation Type / Profession | Customer profession | `customer_master.profession` |
| New Policy | Proposer State/City/Pin/Address | Customer address fields | `customer_master.state`, `customer_master.city`, `customer_master.pin`, `customer_master.address` |
| New Policy | Frame No. | Vehicle frame/chassis | `vehicle_master.chassis` |
| New Policy | Engine No. | Vehicle engine | `vehicle_master.engine` |
| New Policy | Model Name | Vehicle model | `vehicle_master.model` |
| New Policy | Year of Manufacture | Vehicle year | `vehicle_master.year_of_mfg` |
| New Policy | Fuel Type | Vehicle fuel | `vehicle_master.fuel_type` |
| New Policy | Ex-Showroom | Vehicle price | `vehicle_master.vehicle_price` |
| New Policy | RTO | Dealer RTO mapping | `dealer_ref.rto_name` |
| New Policy | Nominee Name/Age/Relation | Insurance nominee details | `insurance_master.nominee_name`, `insurance_master.nominee_age`, `insurance_master.nominee_relationship` |
| New Policy | Nominee Gender | Customer-linked nominee capture | `customer_master.nominee_gender` |
| New Policy | Financer Name | Finance context from details sheet | `customer_master.financier` |

### 6.7 Download/Save Outputs

- DMS Reports must save:
  - `form21.pdf`
  - `form22.pdf`
  - `invoice_details.pdf`
- Automation trace files must save:
  - `ocr_output/<dealer>/<subfolder>/DMS_Form_Values.txt`
  - `ocr_output/<dealer>/<subfolder>/Vahan_Form_Values.txt`
- Insurance run should also persist a form trace artifact when implemented:
- Insurance run must persist a form trace artifact:
  - `ocr_output/<dealer>/<subfolder>/Insurance_Form_Values.txt`

### 6.8 Video and automation reconciliation

- **DMS:** The **§6.1a** sequence reflects the Hero Connect / Siebel operator recording (login, Enquiry, receipt, PDI, booking/allocation, payments, invoice/report). **Playwright parity** (what is implemented today vs §6.1a) is maintained in **LLD §2.4d** and should be updated when `siebel_dms_playwright` or the dummy flow changes.
- **Insurance:** Page order and core labels are video-aligned; final field-level optional add-on choices still require implementation-time confirmation.

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
| 1.3 | Mar 2026 | — | Updated Add Sales UX: removed financing upload/Fill Forms finance section; added Section 2 finance field and new customer fields (`financier`, `marital_status`, `nominee_gender`) sourced from details sheet |
| 1.4 | Mar 2026 | — | Added insurance video-aligned navigation sequence and page-label to DB-column mapping contract; added planned Insurance form-value trace output |
| 1.5 | Mar 2026 | — | Added Insurance Playwright functional rule: DB-only fill, operator-login fallback, no final submit click, and browser session kept open |
| 1.6 | Mar 2026 | — | Updated Add Sales UI action flow to separate `Fill DMS`, `Fill Insurance`, and bottom `Print Forms` trigger |
| 1.7 | Mar 2026 | — | Added Alternate/Landline field contract: capture from details sheet, persist as `customer_master.alt_phone_num`, and use for DMS/Insurance landline fills |
| 1.8 | Mar 2026 | — | Extended DMS Playwright sequence (enquiry/stock/PDI/allocate/invoicing line), ex-showroom → `vehicle_price`, operator-only Create Invoice, BR-14–BR-16, and `form_dms_view` / customer DMS fields |
| 1.9 | Mar 2026 | — | BR-18: Aadhaar back / freeform parsing for double-dash state–PIN OCR, trailing state+PIN without `DIST:`, and `Address:` blocks that continue on the `C/O:` line |
| 2.0 | Mar 2026 | — | FR-23: Aadhaar OCR fallbacks — **DOB-anchored gender** (skip token, `/`, gender) and **comma + dash-run** state/PIN before last PIN |
| 2.1 | Mar 2026 | — | FR-18: **DMS_MODE** / **DMS_REAL_URL_*** for Hero Connect Siebel vs dummy HTML; settings API exposes mode |
| 2.2 | Mar 2026 | — | **§6.1a** Hero Connect / Siebel target DMS automation checklist (ordered); **§6.8** reconciliation pointer to LLD §2.4d; **FR-18** cross-reference |
| 2.3 | Mar 2026 | — | **FR-18** updated for real Siebel §6.1a implementation (`skip_find` + default Find branch + vehicle split) |
| 2.4 | Mar 2026 | — | **§6.1a** step 4a: **Pre Check** before PDI on In Transit branch |
