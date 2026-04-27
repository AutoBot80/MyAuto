# Business Requirements Document (BRD)
## Auto Dealer Management System — Arya Agencies

**Version:** 3.6  
**Last Updated:** April 2026  
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
| BR-6 | Service reminders | When dealer has auto_sms_reminders = Y, **INSERT or UPDATE** on `sales_master` runs trigger `fn_sales_master_sync_service_reminders` (`DDL/09_trigger_sales_master_sync_service_reminders.sql`), which refreshes `service_reminders_queue` from `dealer_ref` + `oem_service_schedule`. **Single writer:** the queue is **trigger-maintained only** — application code must **not** INSERT or UPDATE `service_reminders_queue` (avoids duplicating trigger logic and drifting behavior). |
| BR-7 | Form 20 data | Form 20 fields populated from customer_master, vehicle_master, dealer_ref; vehicle data merged from DB when vehicle_id provided. |
| BR-8 | Bulk failure visibility | Bulk Upload `Error` and `Rejected` records must remain in the hot dashboard until the operator marks `action_taken=true`; older rows are not archived. |
| BR-9 | Automation source of truth | **Vahan** Playwright fills must read site field values from `form_vahan_view` only. **DMS (Create Invoice)** must use **`add_sales_staging.payload_json`** (OCR merge) plus values **scraped during the DMS run** — **not** `customer_master`, `vehicle_master`, or `sales_master`. A legacy path with **`customer_id` + `vehicle_id`** may still load the same projection from masters until fully retired. |
| BR-10 | Automation trace output | DMS and Vahan automation must write `DMS_Form_Values.txt` and `Vahan_Form_Values.txt` into the matching `ocr_output/<dealer>/<subfolder>/` folder. |
| BR-11 | Admin data reset preservation | Admin-triggered data reset must preserve `oem_ref`, `dealer_ref`, and `oem_service_schedule` while clearing all other public base tables. |
| BR-12 | Operator-assisted browser session requirement | DMS and Vahan automation should first reuse already open, logged-in site tabs; when no detectable tab is available, the system should open Edge/Chrome for the operator and ask the operator to complete login (first-time) before retrying automation. |
| BR-13 | Automation value discipline | Playwright must never assume, infer, remember, or default fill values for DMS/Vahan fields. Values sent to site fields must come directly from the approved sources in **BR-9** (DMS: staging JSON or master join in app code; Vahan: `form_vahan_view`) and related persisted columns only. |
| BR-14 | DMS booking test budget | Dummy / training DMS flow uses a fixed **customer budget / enquiry amount of 89000** for booking generation unless the business replaces this constant in automation. |
| BR-15 | Ex-showroom vs column name | **Order Value / ex-showroom** from DMS is persisted as `vehicle_master.vehicle_ex_showroom_price` (no separate `vehicle_cost` column). `form_vahan_view` still exposes the value as `vehicle_price`. Labels in exports and dummy UI read **Ex-showroom Price**. |
| BR-16 | Create Invoice automated | DMS Playwright clicks **Apply Campaign** then **Create Invoice** at the end of `_attach_vehicle_to_bkg`; scrapes **Invoice#** on success. |
| BR-17 | Aadhaar gender default | When **gender** is not extracted from **AWS Textract** (Aadhaar front/back text) and parsers for a subfolder that includes `Aadhar.jpg` and/or `Aadhar_back.jpg`, the persisted `customer.gender` in `OCR_To_be_Used.json` defaults to **Male** (operators may correct before submit). |
| BR-18 | Address-derived locality | When **state**, **PIN**, or **care_of** is missing but **address** contains **`C/O:`** (Care of), **`DIST: <District>, <State> - <PIN>`** (including OCR variants like **`<State> - - <PIN>`** or **`<State> -- <PIN>`**), a trailing **`<Indian state> - <PIN>`** when **`DIST:`** is unreadable, and/or a 6-digit PIN, the system infers **`care_of`**, **`city`/district**, **`state`**, **`pin`**; drops text **after the PIN**; strips **C/O** from the stored address line. Applied on Submit Info, OCR JSON, Aadhaar back parsing, and DMS fill when address/state/PIN fields are sparse. |
| BR-19 | Siebel Contact Find and Add Enquiry persistence | On **real** Hero Connect / Siebel (`DMS_MODE=real`), Contact **Find** uses **Mobile** + **Contact First Name**; first name must be present and must not be a placeholder (**§6.1b**). The **First Name** query field is typed **exactly** (no wildcard). **Grid / automation row detection** after Find uses the **legacy** Hero-aligned rules (**§6.1b**): first-token and prefix matching on row text and cells, optional **mobile-only** acceptance when the name is not in the DOM, plus duplicate-row handling — not strict cell-only exact equality. **Open enquiry** (incl. **Enquiry Status** = Open when HHML fields exist), the **Enquiry# post-save gate** (timed polls) on **Add Enquiry**, and **video SOP orchestration** (**LLD 6.67**): **N=0** mobile drilldown rows → **Add Enquiry**; else title sweep for **Open**; if **no Open**, branch **(2)** fills **Home Phone #**, **Email** (default **`na@gmail.com`** when not overridden in DMS values), selects **Address** on the **Third Level View Bar** (**`select#j_s_vctrl_div_tabScreen`**, **`tabScreen6`**) when present, then **Address** under **`#s_vctrl_div`** / fallback, then **City** (**`input[name="City"]`** / **`id=1_City`**, Siebel LOV) and **Postal Code** (jqGrid **`1_s_1_l_Postal_Code`**, **`SWE_Form1_0`**, **`#SWEApplet1`** / **`div#S_A1`**; **`iframe#S_A1`** first when used), then **Ctrl+S** + Save fallback (**LLD 6.246**–**6.253**); **dotted suffixed first name** is **not** used on the **video** path. Further normative detail in **§6.1b**. |
| BR-20 | Generate Insurance inputs | **Generate Insurance** runs only after **Create Invoice** has persisted **`sales_master`**, **`customer_master`**, and **`vehicle_master`** (commit wave after successful DMS), so **`form_insurance_view`** returns the sale-linked projection. **`add_sales_staging.payload_json`** holds the merged OCR / operator snapshot from Submit. **Together** — view + staging — are the **complete approved input set**. The Add Sales client passes **`customer_id`** and **`vehicle_id`** from the **Create Invoice** response (or legacy flow) and the same **`staging_id`** (**`insurance_form_values.build_insurance_fill_values`**). **`OCR_To_be_Used.json`** is used **only** as a last-resort **insurer** fallback when both view and staging lack insurer. **No** **`insurance_master`** write on Submit; on **successful** Generate Insurance, the backend **INSERT**s **`insurance_master`** for the current calendar **`insurance_year`** (**fails** if **`(customer_id, vehicle_id, insurance_year)`** already exists). Nominee/insurer from fill dict; **policy number** and **`insurance_cost`** from the **policy preview** before **Issue Policy** when scraped; other policy fields from staging when present. Playwright then clicks **Issue Policy** and scrapes **policy number** and **`insurance_cost`** again; **`update_insurance_master_policy_after_issue`** updates those columns on the same row (operators are not expected to pay/issue twice for the same sale/year). |
| BR-21 | DMS Run Report PDFs (post–Create Invoice) | After a successful **staging-path** DMS run with a scraped **Invoice#** and master commit, automation may run Siebel **Report(s)** → **Run Report** and download a **default** batch of reports (**GST Retail Invoice** first, then **GST Booking Receipt**) into **`ocr_output/<dealer_id>/<subfolder>/`**, each file named **`{mobile}_{Report_Name}.pdf`** (report title sanitized; spaces → underscores). Spurious Siebel download events (e.g. UUID filenames) are deprioritized in favor of real **`.pdf`** candidates. One report failing does not necessarily stop the rest (**`continue_on_report_error`**). **`POST /fill-forms`** and **`POST /fill-forms/dms`** return structured status under **`hero_dms_form22_print`** when present (paths, per-report **`ok`** / **`error`**). |
| BR-22 | Subdealer challan DMS | **Subdealer Challan** is a **separate** automation path from **Add Sales** (retail **Find Contact Enquiry**). It persists a batch header in **`challan_master_staging`** (**`challan_batch_id`**, **`num_vehicles`**, **`num_vehicles_prepared`**, **`invoice_complete`**, **`invoice_status`** = Pending / Failed / Completed) and one row per vehicle in **`challan_details_staging`** (**Queued** → **Ready** / **Failed** → **Committed**), runs **`prepare_vehicle`** per **Queued** line (raw chassis/engine; no retail contact sweep), upserts **`vehicle_inventory_master`** (**`dealer_id`** = **to_dealer_id**; **`from_company_date`** on insert only; **`sold_date`** / **`yard_id`** unchanged), sets per-line **discount** from **`subdealer_discount_master_ref`**: **sending** dealer **`dealer_id`** = **`from_dealer_id`**, **receiving** dealer’s **`dealer_ref.subdealer_type`** ( **`to_dealer_id`** ) = **`subdealer_type`**, **`valid_flag=Y`**, and **model** = **start** of the DMS model (full DMS value may be longer; **longest** registered **model** if several prefixes match); if no matching row, **1500.00**; then one batch **`prepare_order`** with **`order_line_vehicles`** (chassis + per-line discount). Siebel identity for the booking uses dummy mobile **`0000000000`**, **`dealer_ref`** for **to_dealer_id** (Network customer / institution name), Comments **`From <from_dealer_id>. Helmet credited`**, and **skips** retail **Contact Last Name F2** — **LLD §2.4e**. Final persist: **`challan_master`** + **`challan_details`** (sums for ex-showroom and discount). |
| BR-23 | Bulk consolidated PDF — raw archive and page splits | For **bulk** pre-OCR, input is **PDF only** in the **`raw/`** archive: the consolidated PDF is copied under **`Uploaded scans/{dealer_id}/{mobile}_{ddmmyy}/raw/`** together with **`page_NN.pdf`** (one single-page PDF per original page; **orientation** is applied via **PDF page rotation** after Tesseract OSD on a render — **no** **`page_NN.jpg`** or other rasters under **`raw/`**). **Classification** still uses **in-memory** rasterization from the PDF for Tesseract text (not persisted in `raw/`). Normalized sale documents for downstream Textract (**`Aadhar.jpg`**, **`Details.jpg`**, etc.) are written **outside** `raw/`. **Aadhaar:** separate front/back pages → one file per slot as today; **same page** (combined) → software split (consolidated top/bottom, then letter scissor fallback); **physical layout** normalization is **not** repeated in **`sales_ocr_service`** (Textract only there). |
| BR-24 | Scan orientation (upright) | The system shall **detect** orientation (upright vs 90° / 180° / 270°) using **Tesseract OSD** when **orientation confidence** is sufficient. **Bulk `raw/`:** apply correction via **PDF page rotation** on each **`page_NN.pdf`**. **In-memory** rasters used for Tesseract classification and for building normalized **`*.jpg`** outputs are deskewed the same way. **Add Sales** JPEG uploads: rotate image bytes before Textract. Requires **`osd`** trained data. Low-confidence OSD leaves content unchanged. |

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
- **FR-8** Add Sales flow: Submit Info (customer, vehicle, insurance) → **draft** **`add_sales_staging`** (**`staging_id`**) only → operator-triggered **Create Invoice** (DMS), **Generate Insurance**, **Print Forms and Queue RTO** → RTO Queue insertion during print when IDs exist. **Create Invoice** / **Print Forms** activate after Submit returns **`staging_id`**. **Generate Insurance** additionally requires **`customer_id`** / **`vehicle_id`** returned from a successful **Create Invoice** (post-commit masters). **Create Invoice** and **Generate Insurance** pass **`staging_id`** for **`payload_json`** merge with views (**BR-20**). **Eligibility** (`GET /add-sales/create-invoice-eligibility`): **Create Invoice** allowed when there is **no** matching **`sales_master`** row for the resolved **`vehicle_master`** (**`raw_frame_num`** / **`raw_engine_num`**) + **`customer_master`** (**`mobile_number`**) pair, **or** the row exists with blank **`invoice_number`** (no **`dealer_id`** in the match). **Generate Insurance** when a **sales** row exists, **invoice** is recorded, and no **`insurance_master`** row for that sale has non-empty **`policy_num`**. **Print Forms and Queue RTO** gating unchanged in spirit (**New** / Submit / automation / print interplay per UI). **`customer_master.dms_contact_id`** optional Siebel Contact Id. Masters upsert: **after** successful Create Invoice (**FR-17**, **LLD §2.2a**).
- **FR-9** RTO Queue page: list queued RTO work items created by Fill Forms and let an operator process the oldest 7 queued rows for their dealer in one browser session.
- **FR-10** View Customer page: search by mobile/plate, view vehicles and insurance, and inspect the selected vehicle's Vahan field mapping in a single horizontally scrollable row.
- **FR-10a** Main page: show an `Admin Saathi` tile with a destructive-action button that lets an operator clear all non-reference-table data after explicit confirmation.

### 5.2 Server / Backend

- **FR-11** Expose REST APIs for dealers, vehicles, customers, sales, documents, and the RTO queue.
- **FR-12** Accept document uploads, store files (local or S3), and create OCR jobs.
- **FR-13** Process OCR jobs (Tesseract), parse results, and persist structured data.
- **FR-14** Accept automation requests, enqueue them through SQS or the local in-process fallback queue, and track status.
- **FR-15** Run Playwright workers that log into external portals and submit data from approved sources (DMS: staging or master-backed fill row in app code; Vahan/insurance: views and records as specified).
- **FR-16** Persist all business data in PostgreSQL with clear ownership (e.g. dealer_id) for multi-tenant use.
- **FR-17** Submit Info (**`POST /submit-info`**): validate and write or update a **draft** **`add_sales_staging`** row only (merged **`customer`**, **`vehicle`**, **`insurance`**, **`dealer_id`**, **`file_location`**); response **`staging_id`**. **`customer_master`**, **`vehicle_master`**, **`sales_master`**, and **`insurance_master`** are **not** written on Submit; after **successful Create Invoice (DMS)**, **`add_sales_commit_service`** **upserts** **`customer_master`** / **`vehicle_master`** and **inserts** **`sales_master`** (**fails** if **`(customer_id, vehicle_id)`** already exists — **§6.1d**). **`insurance_master`** persists after **Generate Insurance** as elsewhere. **LLD §2.2a**.
- **FR-18** Fill DMS: Playwright reads DMS fill values from **OCR merge in `add_sales_staging`** (target) or, until staging is wired end-to-end, the **same projection as the former `form_dms_view`** loaded via **`form_dms.py`** (inline `sales_master` + `customer_master` + `vehicle_master` join after Submit Info). **`form_dms_view` is not used.** **Target real-DMS step order** is **§6.1a**; **normative Contact Find, grid match, open enquiry, suffixed first name, and Enquiry# save gate:** **§6.1b** (and **BR-19**); implementation parity: **LLD §2.4d** / **LLD** changelog **6.8**. Static training DMS HTML and **`DMS_MODE=dummy`** were removed; **`DMS_MODE`** defaults to **real** Siebel. **`"DMS Contact Path"`** / **`skip_find`** in persisted data **does not** skip Contact Find on real Siebel — automation **always** runs Find first (`DMS_REAL_URL_CONTACT`, **mobile + Contact First Name** + Go; empty or placeholder first name fails — **§6.1b**); **`skip_find` is not a bypass**. **Generate Booking** runs **after** vehicle processing; allotment when **not** In Transit (see **LLD §2.4d**). **After find:** existing match → care-of only + Save; no match or **`new_enquiry`** → basic enquiry + Save + mandatory re-find + care-of; vehicle scrape with **In Transit** detection; branch receipt → **Pre Check** → **PDI** vs booking/allotment per **§6.1a**. Implementation: **`siebel_dms_playwright.run_hero_siebel_dms_flow`**; **`DMS_Form_Values.txt`** / per-run **`Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt`** (IST, under the sale OCR subfolder); tune **`DMS_SIEBEL_*`** and **`DMS_REAL_URL_*`**. **Merge rule:** Fill DMS persists scraped **full** chassis/engine and related fields into `vehicle_master` but **does not overwrite** `raw_frame_num` / `raw_engine_num` (those stay Submit Info / Sales Detail Sheet so partial VIN/engine for Siebel vehicle search match operator entry).
- **FR-18a** Existing tab reuse with operator fallback: DMS/Vahan steps first reuse already open logged-in tabs; if none are detectable, API opens Edge/Chrome to the target site and returns a user-facing message asking the operator to login (first-time) and retry.
- **FR-18b** Insurance fill step: Playwright fills Insurance portal fields from **`form_insurance_view`** (after sale rows exist) merged with **`add_sales_staging.payload_json`** for the same **`staging_id`** (**BR-20**). Add Sales always supplies **`staging_id`** with **Generate Insurance** so the OCR snapshot and committed masters are used together. **`OCR_To_be_Used.json`** is only an insurer fallback when view and staging both lack insurer. Reuses an already open logged-in insurance tab (or opens browser and asks operator to login first-time) and keeps the browser open for operator review. After the **proposal form** is filled, **`add_sales_commit_service.insert_insurance_master_after_gi`** **INSERT**s **`insurance_master`** for the current calendar year (**fails** if **`(customer_id, vehicle_id, insurance_year)`** already exists — **`uq_insurance_customer_vehicle_year`**), with policy fields from staging when no preview is passed at insert. **Proposal Review** / **Preview** navigation, preview text scrape, and **chkAgree** / **chkconsentagree** run in production only (**`HERO_MISP_CLICK_PROPOSAL_PREVIEW_REVIEW`**); there is **no** separate **`insurance_master` UPDATE** from the proposal preview scrape, and the best-effort **Print Proposal** control is not automated. **`click_issue_policy_and_scrape_preview`** (with optional **Issue Policy** click pause) performs the post–Issue Policy preview scrape; **`add_sales_commit_service.update_insurance_master_policy_after_issue`** runs **once** to refresh **`policy_num`**, **`policy_from`**, **`policy_to`**, **`premium`**, and **`idv`**. **MISP Print Policy** (**`hero_insure_reports_service.run_hero_insure_reports`**): on the same MISP session, after a successful run with scraped **`policy_num`** and merged **`insurer`**, automation opens **Policy Issuance** → **Print Policy** (**`AllPrintPolicy.aspx`**), sets **Product** (longest-prefix match to **`ddlProduct`**), **Policy No.**, **Go**; the grid **Print** opens **Print Policy Certificates**; a second **Print** in that window saves **`{mobile}_Insurance_<ddmmyyyy>.pdf`** (calendar date in **IST**, **Asia/Kolkata**) under **Upload-scans** and uses **`upload_scans_pdf_dispatch`** for print/open; the Generate Insurance response includes **`hero_insure_reports`**. **`premium`** is the single premium monetary column (**`insurance_cost`** removed from schema — **`DDL/alter/14c_insurance_master_drop_insurance_cost.sql`**).
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
- **FR-23** QR decode: Decode Aadhaar UIDAI QR to extract customer details; support QR on **front (`Aadhar.jpg`) and/or back (`Aadhar_back.jpg`)**, merging fields with front preferred when both are readable. **Order of operations (scans-v2):** local QR on both images runs **in parallel with** AWS Textract prefetch where configured; **Aadhaar assembly** (QR + **Textract only** on front/back for printed text — **no Tesseract** on Aadhaar) runs **in parallel with** **Sales Detail sheet** parsing, then JSON is merged once. If QR does not yield a usable identity, **Textract full text** on the front is also parsed for a **heuristic name line** (in addition to **DOB** and **gender**). When address/state/PIN are still weak, **Textract on the back** supplements **printed English address** (e.g. “Near Gayatri School …”). The upload must **not** hard-fail solely for missing QR if Textract can populate fields; otherwise the UI shows **`extraction_error`**. **Gender** uses a **DOB anchor** (after `dd/mm/yyyy`, skip one token, next `/`, then gender), with fallbacks for **`Gender:`** / **Sex/** / **yes/** OCR. **State/PIN** use **comma-separated clauses** and **dash runs** before the last 6-digit PIN, plus **`DIST:`** and trailing-state patterns; back **`Address:`** blocks include the following **`C/O:`** line. The API may return **`extraction.section_timings_ms`** so operators can see time spent per stage (prefetch, parallel Aadhaar+Details, merge, insurance, Raw_OCR).
- **FR-24** Vision / Textract: Optional AI extraction from documents.
- **FR-25** Subdealer Challan (POS Saathi): **Upload scan(s)** allows one or more **PDF and/or image** (JPEG, PNG, WebP) files of the Daily Delivery Report; the client issues **`POST /subdealer-challan/parse-scan` once per file**, then **merges** the OCR results: **challan book number** = **greatest** parsed value when all are digits, otherwise a stable max-style comparison; **lines** in **file order** with the grid de-duplicating engine+chassis pairs (**first** kept). **Challan date** follows the first file with a parseable date; a warning is shown if a later file’s date **differs** (staging still uses the **anchor** file’s date fields). The operator selects a **to_dealer (subdealer)**; **Create Challans** inserts one **`challan_master_staging` + `challan_details_staging` batch** (**`POST /subdealer-challan/staging`**) and runs one DMS batch (**`POST /subdealer-challan/process/{challan_batch_id}`**, long-running like Fill DMS) for the combined vehicle list. The **Processed** tab lists recent batches (**`GET /subdealer-challan/staging/recent`**) with optional **failed** detail lines under each batch; **retry line** (**`POST /subdealer-challan/staging/{challan_detail_staging_id}/retry`**) re-runs prepare + order for the batch after resetting that line to **Queued**. **Retry order** (**`POST /subdealer-challan/batch/{challan_batch_id}/retry-order`**) runs the order/invoice phase only when all detail lines are **Ready** but the batch **invoice** step failed (does not re-run **`prepare_vehicle`**). **`GET /subdealer-challan/staging/failed-count`** supports the navigation badge (count of **Failed** detail lines in the window). Does **not** use **`add_sales_staging`** or retail **`prepare_customer`** — **BR-22**, **LLD §2.4e**.
- **FR-26** Bulk pre-OCR (consolidated PDF): Worker **`run_pre_ocr_and_prepare`** uses **in-memory** rasterization for Tesseract **OSD** and per-page text classification; copies the **source PDF** into **`…/raw/`** and exports **`page_NN.pdf`** only (single-page PDFs with orientation via **page rotation** — **no** raster files in **`raw/`**); then writes normalized **`Aadhar.jpg`**, **`Details.jpg`**, etc. **outside** `raw/` for Textract. **`process_bulk_pdf`** consumes the sale folder directly (no second copy from Processing). **FR-26** aligns with **BR-23** / **BR-24**.

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

This is the **intended** real-DMS order (aligned with the operator screen recording and dealer workflow). Values on every fill step must come only from the DMS sources in **BR-9** / **BR-13** (staging OCR merge or master-backed fill row). **Create Invoice** is automated via `_attach_vehicle_to_bkg` (BR-16). Playwright implementation parity is tracked in **LLD §2.4d**.

| Step | Siebel module / screen (typical labels) | Action |
|------|----------------------------------------|--------|
| 0 | **Hero Connect** (session) | Operator is signed in. Automation reuses a logged-in tab when possible (BR-12); otherwise opens browser and waits for operator login before continuing. |
| 1 | **Contacts** (or Buyer/CoBuyer contact view — `DMS_REAL_URL_CONTACT`) | If **Find** is collapsed, expand it; set **Find** object type to **Contact**; enter **Mobile** and **Contact First Name** (both required on real Siebel — **§6.1b**); run query (**Go** / **Find**). |
| 2a | **Contact / Enquiry — not found** | **Preferred (real Siebel):** when **Find** returns **no table row matching mobile + first name** (**§6.1b**), vehicle **Find→Vehicles** + VIN/engine query + scrape (model, **YYYY** year, color), then **Enquiry** → **Opportunities List:New** → fill from **DMS fill source** (staging OCR or master join) **+ scrape**: **Contact First/Last Name**, **Mobile Phone**, **Landline** (alternate), **UIN Type** Aadhaar, **UIN No.** (last 4), **State**, optional **District** / **Tehsil** / **City** / **Age** / **Gender** when present in DB, **Address Line 1**, **Pin Code**, **Model Interested In** / **Color** from scrape, **Finance Required** Y/N (from **`Finance Required`** column or financier populated), **Booking Order Type** Normal Booking, **Enquiry Source** Walk-In, **Point of Contact** Customer Walk-in, **Actual Enquiry Date** (today **dd/mm/yyyy**); **do not** require **Financier** fields; **Ctrl+S**. **Fallback:** basic enquiry form + **Save** if this path cannot complete. |
| 2b | **Contact / Enquiry — found** | When **one or more** rows match **mobile + Contact First Name** (**§6.1b**), use the **first** row (top-to-bottom order) that has an **open enquiry** on tab **Contact_Enquiry** (≥1 populated list row — **§6.1b**). If **no** matching row has an open enquiry, create a **new opportunity** with **suffixed** Contact First Name (`.`, `..`, … — **§6.1b**), then re-find and open that contact. Then update **S/O or W/o** and **Father / Husband / Care of** (and related care-of fields) from DB-backed values only; **Save**. Do not replace the whole customer record unless the product owner explicitly extends this rule. |
| 3 | **Vehicles → Auto Vehicle List** (`DMS_REAL_URL_VEHICLE`) | **Find→Vehicles** with **`*`**-prefixed **VIN (chassis)** and **Engine** partials (same fly-in as Add Enquiry); execute find; scrape the list grid row; **open the vehicle from left Search Results** via the **Title** drilldown (automation **requires** a matching chassis and a successful drill-in — **LLD** **6.72**). Then **Key/Battery** on the vehicle form, merge **Vehicle Information**, read **Inventory Location** to decide **In Transit** vs dealer stock. **Dealer** path: top vehicle grid **Serial Number** drilldown (after optional **VIN** drilldown), **Features** (cubic / vehicle type), then tab **Pre-check** and **PDI**. **Key** partials are for the vehicle form, not as a fallback list search (**LLD** **6.51**). |
| 4a | **Branch: In Transit** | **Vehicles Receipt → HMCL – In Transit** (or tenant-equivalent): **Process Receipt** for the VIN when automation finds the control. **Playwright `prepare_vehicle`** stops there for in-transit stock — it **does not** drive **`DMS_REAL_URL_PRECHECK`** / **`DMS_REAL_URL_PDI`** (Siebel rejects Pre-check/PDI until the unit is at **dealer** stock). Operator completes inspection after receipt or once inventory shows dealer. **Dealer** stock uses **one** tab Pre-check/PDI path on the vehicle form (serial-detail); see **LLD** changelog **6.48**. |
| 4b | **Branch: not In Transit (booking path)** | **Enquiry → My Enquiries** (or current enquiry): **Generate Booking** when creating/linking the sales order. **Vehicle Sales → My Orders:** before **Sales Orders List:New (+)**, query by customer **mobile** on the My Orders Find (**`s_1_1_1_0`** → **Mobile Phone#** → Enter) and branch on **jqGrid** rows (meaningful **Invoice#** → stop for client **Create Invoice**; **Pending** / **Allocated** → open **Order#** and continue attach per **LLD** **6.115**; empty grid → full **+** booking). Then **line items:** for each vehicle, **New** → **VIN** → optional **Discount**; then **Price All** / **Allocate All** once (multi-line list from DMS **`order_line_vehicles`** / **`attach_vehicles`** when provided — **LLD** **6.278**). |
| 5 | **Tenant-dependent gates (if shown)** | Handle or stop for operator: **Vehicle Digitization** (e.g. OTP), **Document Upload**, **Sanction Details**, **Validate GL Voucher**, **WOT Details** (if exchange), **Contacts → Payments**, finance/hypothecation dialogs. No invented clicks — follow persisted flags and visible prompts. |
| 6 | **Invoice + reports** | **Create Invoice** is driven in the booking-attach path per **BR-16** (**Apply Campaign** → **Create Invoice** when automation is enabled). After **`sales_master`** / masters commit from a scraped **Invoice#**, automation may run **Report(s)** → **Run Report** and download the default GST PDF batch per **BR-21**. Leave the **browser window open** for operator review (same session discipline as insurance automation). |

### 6.1b Real Siebel Contact Find & Add Enquiry Rules (Normative)

1. **Contact Find inputs (required):**
   - Fill **Mobile** using field `title="Mobile Phone"` and **First Name** using `id="field_textbox_1"` **within the same frame**.
   - First Name is mandatory. Empty/whitespace or placeholders (for example: `NA`, `N/A`, `null`, `none`, `-`, `.`, `..`) fail the run.
   - The value typed into **First Name** for the query is the **exact** string from automation (no **`*`** wildcard); trailing dot suffixes used for duplicate-contact keys are stripped before typing and before comparing to grid cells.
2. **Search key and row eligibility:**
   - The **Find** query uses **mobile** plus the **exact** first name string (see **1**). **After** Find, automation decides whether a **table hit** exists using **mobile** plus **first-name fuzzy rules** consistent with Hero CRM: **case-insensitive** matching with **first-token / prefix / row-text** heuristics on cells and **textContent**, and a **mobile-only** fallback when Siebel shows the number (e.g. under **Title**) but omits the first name from the DOM — so automation can still treat the row as a hit and proceed to drill/open.
   - If **0 rows** qualify under those heuristics, run Add Enquiry with the **base first name** (no dot suffix).
3. **Open enquiry decision for duplicates:**
   - If one or more rows match (including **two+ rows with the same mobile in the same list frame**), automation **drills each row in order** using **in-place** row clicks (split UI: **Search Results** stay open — **no** Contact Find re-run between rows), then on **each** opened contact switches to tab **Contact_Enquiry** and inspects the enquiry subgrid. If an in-place drill for a later duplicate row fails, the sweep **stops** for that path (no second drill and no re-Find for that row).
   - A contact has an **open enquiry** when that subgrid shows **at least one populated** Enquiry# (non-empty **`input`/`textarea` `name="Enquiry_"`**; header may be **`div#jqgh_s_1_l_Enquiry_`** / **Enquiry#** in Open UI). Evaluation prefers the **main document** when it contains a hit, then Siebel iframes.
   - If multiple matching rows have open enquiry, select the **first** one and continue normal relation/payment/order flow.
   - After drilling row *k* and finding no enquiry, automation continues with the **next** duplicate row via **in-place** click (ordinal *k+1* with **mobile-only** row matching for ordinals ≥ 1). No navigate-back and **no** re-run of Contact Find between those drills.
4. **No open enquiry in any matching row:**
   - Create a new enquiry with suffixed first name by appending dots: `first.`, then `first..`, etc.
   - Re-find by mobile + suffixed first name, drill into that contact, then continue to relation fill and downstream steps.
5. **Post-save Enquiry# hard gate (Ctrl+S):**
   - Capture Enquiry# before save.
   - Poll Enquiry# after save at **0.5s**, **2.5s**, and **3.5s**.
   - Success requires Enquiry# to **change** from pre-save value within these polls.
   - If Enquiry# remains unchanged, treat as **hard failure** and stop.
6. **Required logging:**
   - Log pre-save Enquiry#, each timed poll value, selected contact row index for duplicate matches, and first-name suffix used (`.`, `..`, ...).
7. **Vehicle Find field selectors (after Find → Vehicles):**
   - Fill **VIN** using `id="field_textbox_0"` and **Engine#** using `id="field_textbox_2"` in the **same frame**.
   - If those same-frame controls are not available/fillable, stop with failure (do not fall back to looser selector inference).

### 6.1c Vehicle master (reference)

- **`chassis`** / **`engine`**: full VIN and full engine from the Siebel **Vehicles** record scrape (**`full_chassis`**, **`full_engine`**).
- **`key_num`**: same as **`raw_key_num`** on initial master commit.
- **`variant`**: scraped from the Vehicles page (`varchar(64)`).
- **`model`**, **`colour`**, **`year_of_mfg`**, **`cubic_capacity`**: from the Vehicles page / sub-page per automation; **`vehicle_type`** is normalized to **ALL CAPS** for storage.
- **Two-wheeler:** when normalized **`vehicle_type`** contains **MOTORCYCLE** or **SCOOTER**, set **`seating_capacity`** = **2**, **`body_type`** = **Open**, **`num_cylinders`** = **1**.
- **`place_of_registeration`**, **`oem_name`**: from **`dealer_ref.rto_name`** and **`oem_ref.oem_name`** for the sale’s dealer (via latest **`sales_master`**), not from the vehicle UI.
- **`vehicle_ex_showroom_price`**: scraped after **Price All** / **Allocate All** in the booking-attach path (**`_attach_vehicle_to_bkg`**). Multiple order lines: per-row values may appear in scrape output **`order_line_ex_showroom`**; the scalar field used for legacy merge remains the first valid line’s ex-showroom (**LLD** **6.278**).
- **Uniqueness:** keep **`uq_vehicle_raw_triple`** on raw columns and enforce a **partial unique index** on populated **`chassis`** (canonical VIN). Column **`dms_sku`** is **dropped**.

### 6.1d `sales_master` (reference)

- **`order_number`**, **`invoice_number`**, and **`enquiry_number`** are **scraped from Siebel at different points** during the DMS run (enquiry / order / invoice stages — not a single screen). **Master rows are not written until after Create Invoice** (scraped **Invoice#** present); then **`sales_master`** receives those columns on the **initial INSERT** (staging commit or **`insert_dms_masters_from_siebel_scrape`**), not via incremental `UPDATE` during automation.
- **`vahan_application_id`** and **`rto_charges`** are **not** filled during DMS; they are written later by the **Vahan** form-filling / RTO queue processing when application id and fees are scraped from VAHAN.
- **Post–Create Invoice master commit** (`add_sales_commit_service`): **`sales_master`** insert **fails** if a row already exists for the same **`(customer_id, vehicle_id)`** (no upsert / no silent merge on conflict).

### 6.2 DMS Fields to Fill (Minimum Contract)

| Page | Field label | Required source |
|------|-------------|-----------------|
| Enquiry | Contact First Name | DMS fill: **`add_sales_staging.payload_json`** (OCR merge) or master columns via **`form_dms.py`** join |
| Enquiry | Contact Last Name | DMS fill (staging or master join) |
| Enquiry | Mobile Phone # | DMS fill (staging or master join) |
| Enquiry | Landline # | DMS fill — `customer_master.alt_phone_num` when using master path |
| Enquiry | State | DMS fill (staging or master join) |
| Enquiry | Address Line 1 | DMS fill (staging or master join) |
| Enquiry | Pin Code | DMS fill (staging or master join) |
| Vehicle Search | Key num (partial) | DMS fill (staging or master join) |
| Vehicle Search | Frame / Chassis num (partial) | DMS fill (staging or master join) |
| Vehicle Search | Engine num (partial) | DMS fill (staging or master join) |
| Enquiry | Relation (S/O or W/o), Father/Husband name | DMS fill — `customer_master.care_of` (Aadhaar QR); relation prefix from first 3 chars of address else `D/o`/`S/o` by customer gender |
| Enquiry | Financier / Finance Required (invoicing line) | DMS fill — `customer_master.financier` |
| Enquiry | New vs existing CRM contact | DMS fill — `"DMS Contact Path"`: `found` / `new_enquiry` / **`skip_find`** — dummy: `skip_find` skips finder Go; **real Siebel: always runs Find first**; `skip_find` in DB is ignored for automation order |
| Invoicing line | Order Value (Ex-showroom) | Scraped from DMS → `vehicle_master.vehicle_ex_showroom_price` |
| Post-Fill DMS | Enquiry# | Scraped during DMS (enquiry stage) → `sales_master.enquiry_number` |
| Post-Fill DMS | Order# | Scraped during DMS (order stage) → `sales_master.order_number` |
| Post-Fill DMS | Invoice# | Scraped during DMS (invoice stage) → `sales_master.invoice_number` |

### 6.3 Vahan Fields to Fill (Minimum Contract)

- All Vahan fields must be read from `form_vahan_view` labels and DB-backed technical columns (no hardcoded assumptions during runtime).
- **Persist back to `sales_master`:** **`vahan_application_id`** and **`rto_charges`** on the sale row are updated when the Vahan / RTO batch run scrapes them — not during DMS (**§6.1d**).
- Operator workflow remains: logged-in tab reuse first, fallback to auto-open browser and first-time login prompt, then retry.
- **RTO queue batch (workbench automation):** Dealer-scoped processing (`POST /rto-queue/process-batch` and related APIs) runs Playwright against the Vahan **workbench** flow implemented in **`fill_rto_service`** (invoked from **`rto_payment_service`**). The per-row fill dict is built from **`form_vahan_view`** joined with **`insurance_master`** (e.g. financier context, **`nominee_name`**, **`nominee_relationship`**) via the RTO queue repository. Screen 3 targets PrimeFaces controls by stable **`workbench_tabview:*`** ids (e.g. MV Tax **`tableTaxMode:0:taxModeType_input`**, hypothecation panel **`hpa_*`**, nominee radios **`nomineeradiobtn1:0|1`**). **Diagnostics:** full page-state dumps (`_dump_page_state`) are written to the per-run RTO log **only when a step ultimately fails** (e.g. no matching control after all selectors, terminal click/scrape failure), not on every individual selector timeout — **LLD §2.4f**.

### 6.4 Insurance Fields to Fill (Submit Info Contract)

- Insurance and related customer fields from Submit Info are validated and stored in **`add_sales_staging.payload_json`** (`nominee_gender` under **insurance** until **Generate Insurance** commits). **`insurance_master`** is not written on Submit; after **successful Generate Insurance**, the API **INSERT**s **`insurance_master`** for the current **`insurance_year`** (**fails** on duplicate **`(customer_id, vehicle_id, insurance_year)`**), including **`nominee_gender`**; nominee/insurer from the MISP fill dict; **`policy_num`**, **`policy_from`**, **`policy_to`**, **`premium`**, and **`idv`** at insert use staging (and optional preview at insert) as implemented; **`policy_broker`** from staging when present. A **single** **`update_insurance_master_policy_after_issue`** from the post–**Issue Policy** (or paused equivalent) scrape updates those five policy fields (**FR-18b**); there is no second update from the proposal preview alone. On master commit after **Create Invoice**, **`customer_master`** receives profession/financier/marital/care_of/DMS path fields from the staging snapshot (**FR-17**); per-sale **`file_location`** is **`sales_master.file_location`** (mirrored on **`customer_master.file_location`** on commit).
- Hero Insurance: after **Create Invoice**, **`form_insurance_view`** plus **`add_sales_staging.payload_json`** (same **`staging_id`** as DMS) supply the automation inputs — committed sale/vehicle/customer context from the view and the full OCR/operator merge from staging (**BR-20**). **`dealer_ref.hero_cpi`** (**Y**/**N**, on the view as **`hero_cpi`**) drives the MISP proposal CPA add-on row whose label varies (NIC/CPI). **Email, most add-ons, CPA tenure, payment mode, and registration date** may use **hardcoded** defaults in Playwright where not listed here. Insurer may fall back to **`OCR_To_be_Used.json`** only when view and staging lack it.

### 6.5 Insurance Navigation Sequence (Video-Aligned)

1. Login page (`misp.heroinsurance.com` / dummy `index.html`): operator enters credentials and **Login**; Playwright waits (up to `INSURANCE_LOGIN_WAIT_MS`) until KYC is shown, then automates KYC.
2. KYC verification page (`ekycpage.aspx` / dummy `kyc.html`): enter mobile → **Verify mobile**; if KYC not on file, upload three documents (Aadhaar front, rear, customer photo) → consent → **Submit** to advance; dummy uses `#ins-check-mobile` / `#ins-kyc-submit` / `policy.html` flow.
3. KYC success auto-redirect screen
4. MisDMS policy entry page (`MispDms.aspx`) with VIN input
5. New policy creation page (`MispPolicy.aspx`) for "New Policy - Two Wheeler"
6. (Optional reference tab) Hero Connect lookup for invoice/vehicle context, then return to MisDMS policy flow
7. **Proposal form complete** → **`insurance_master` INSERT** (current calendar year; **fails** if that triple already exists). **Proposal Review** (production only): optional **Preview** navigation; preview scrape to trace; **chkAgree** / **chkconsentagree** — **not** a second **UPDATE** from preview, **not** the old best-effort **Print Proposal** click. **Post–Issue Policy** scrape ( **`click_issue_policy_and_scrape_preview`**) → **one** **UPDATE** of policy columns (**FR-18b**).
8. **MISP — Print Policy certificate** (**`run_hero_insure_reports`**): **Policy Issuance** → **Print Policy** → **`AllPrintPolicy.aspx`**: **Product** / **Policy No.** / **Go**; grid **Print** opens **Print Policy Certificates**; second **Print** in that window; PDF saved as **`{mobile}_Insurance_<ddmmyyyy>.pdf`** (IST) under **Upload-scans**; local print dispatch per **`ENVIRONMENT`**.

### 6.6 Insurance Labels to Minimum Data Source Contract

| Insurance page | Label | Required source (DB-backed) | Persisted DB column |
|----------------|-------|------------------------------|---------------------|
| KYC | Insurance Company | Merged details insurer (view + staging + OCR fallback); consent/SMS boilerplate misread as insurer is cleared; if merged insurer is empty and **`dealer_ref.prefer_insurer`** is set, use **`prefer_insurer`**; else if **`prefer_insurer`** is set and the merged string has **≥20%** fuzzy similarity to it, use **`prefer_insurer`** as the portal label; otherwise fuzzy-match merged text to portal options | `insurance_master.insurer` (staging/view); **`prefer_insurer`** on **`dealer_ref`** via **`form_insurance_view`** |
| KYC | KYC Partner | Dealer-configured onboarding value | Reference/config (runtime choice, not persisted in current schema) |
| KYC | Proposer Type | Portal default | Dummy/default **Individual**; Playwright does not change tenure/proposer selects |
| KYC | OVD Type | Document type from scan set | derived from uploaded docs metadata |
| KYC | Mobile No. | Customer mobile | `customer_master.mobile_number` |
| KYC | AADHAAR Front/Rear Image | Uploaded scan artifacts | `uploads/<dealer>/<subfolder>/` files |
| KYC | Customer Photo | Uploaded scan artifacts | `uploads/<dealer>/<subfolder>/` files |
| MisDMS Entry | VIN Number | Vehicle chassis/frame | `vehicle_master.chassis` (or raw frame column) |
| New Policy | Insurance Company* | Same merged insurer string, consent-line sanitization, empty-insurer **`prefer_insurer`** fallback, and fuzzy **`prefer_insurer`** override rule as KYC | `insurance_master.insurer`; dealer **`prefer_insurer`** |
| New Policy | Policy Tenure | Portal default | Dummy option only; Playwright does not set |
| New Policy | Manufacturer* | OEM / make (fuzzy-matched to portal options) | `vehicle_master.oem_name`, else dealer `oem_ref.oem_name` |
| New Policy | Proposer Type* | Portal default | Dummy **Individual**; Playwright does not set |
| New Policy | Proposer Name | Customer name | `customer_master.name` |
| New Policy | Gender | Customer gender | `customer_master.gender` |
| New Policy | Date of Birth | Customer DOB | `customer_master.date_of_birth` |
| New Policy | Marital Status | Customer marital status | `customer_master.marital_status` |
| New Policy | Occupation Type / Profession | Customer profession; when blank after Details sanitization, **Employed** (**`default_profession_if_empty`**) | `customer_master.profession` |
| New Policy | Proposer State/City/Pin/Address | Customer address fields | `customer_master.state`, `customer_master.city`, `customer_master.pin`, `customer_master.address` |
| New Policy | Frame No. | Vehicle frame/chassis | `vehicle_master.chassis` |
| New Policy | Engine No. | Vehicle engine | `vehicle_master.engine` |
| New Policy | Model Name | Vehicle model | `vehicle_master.model` |
| New Policy | Year of Manufacture | Vehicle year | `vehicle_master.year_of_mfg` |
| New Policy | Fuel Type | Vehicle fuel | `vehicle_master.fuel_type` |
| New Policy | Ex-Showroom | Vehicle price | `vehicle_master.vehicle_ex_showroom_price` (via `form_vahan_view.vehicle_price`) |
| New Policy | RTO | Dealer RTO mapping | `dealer_ref.rto_name` |
| New Policy | Nominee Name/Age/Relation | Insurance nominee details | `insurance_master.nominee_name`, `insurance_master.nominee_age`, `insurance_master.nominee_relationship` |
| New Policy | Nominee Gender | Staging until policy commit | `insurance_master.nominee_gender` (`form_insurance_view`) |
| New Policy | Financer Name | Finance context from details sheet | `customer_master.financier` |
| New Policy | CPA Hero CPI add-on (label varies: NIC, CPI, …) | **Y** = check, **N** = uncheck | **`dealer_ref.hero_cpi`** via **`form_insurance_view.hero_cpi`** |
| New Policy | Email / other add-ons / CPA tenure / payment / reg. date (proposal) | Hardcoded Playwright defaults where not DB-backed | Not persisted (optional future columns) |

### 6.7 Download/Save Outputs

- **DMS Run Report PDFs (real Siebel, post–master commit):** default batch **GST Retail Invoice**, **GST Booking Receipt** — saved under the sale **`ocr_output/<dealer_id>/<subfolder>/`** as **`{mobile}_{Report_Name}.pdf`** (**BR-21**). Older placeholder names (`form21.pdf`, `form22.pdf`, `invoice_details.pdf`) are **not** the current automation contract.
- Automation trace files must save:
  - `ocr_output/<dealer>/<subfolder>/DMS_Form_Values.txt`
  - `ocr_output/<dealer>/<subfolder>/Vahan_Form_Values.txt`
- Insurance run should also persist a form trace artifact when implemented:
- Insurance run must persist a form trace artifact:
  - `ocr_output/<dealer>/<subfolder>/Insurance_Form_Values.txt`

### 6.8 Video and automation reconciliation

- **DMS:** The **§6.1a** sequence reflects the Hero Connect / Siebel operator recording (login, Enquiry, receipt, PDI, booking/allocation, payments, invoice/report). **Playwright parity** (what is implemented today vs §6.1a) is maintained in **LLD §2.4d** and should be updated when `siebel_dms_playwright` or the dummy flow changes.
- **Insurance:** Page order and core labels are video-aligned; final field-level optional add-on choices still require implementation-time confirmation.

### 6.9 Subdealer Challan (automation summary)

- **OCR (client):** Multiple DDR pages (or separate PDFs/images) are uploaded together; the API **parses one file at a time**; the client **merges** book number (max) and line items before **one** **Create Challans** — **FR-25**, **LLD** **§2.4e** / **`api/subdealerChallan.ts`**. Per-file size limits match server **binary upload** caps; each **parse-scan** request is independent.
- **Staging:** One **UUID `challan_batch_id`** groups each **Create Challans** action. **`challan_master_staging`** stores the header (**`from_dealer_id`**, **`to_dealer_id`**, book/date, **`num_vehicles`**, aggregates, **`invoice_status`** / **`invoice_complete`**). **`challan_details_staging`** stores one line per vehicle (**raw** engine/chassis, **`status`**, **`last_error`**, **`inventory_line_id`**).
- **Vehicle prep:** For each **Queued** detail line, automation runs **`prepare_vehicle`** (same Siebel vehicle prep as **§6.1a** step 3 for dealer stock, but **no** Add Sales customer context). Failures may be retried after a short wait; line status **Ready** when scrape + **`vehicle_inventory_master`** upsert succeed.
- **Inventory + discount:** **`vehicle_inventory_master`** is keyed by scraped chassis/engine. Per-line **discount** is resolved with **`dealer_ref.subdealer_type`** ( **`to_dealer_id`** ) and **`subdealer_discount_master_ref`** ( **`dealer_id`** = **`from_dealer_id`**, same **`subdealer_type`**, **`valid_flag=Y`**, **model** = **prefix** of DMS model); if none applies, **1500.00** — **Database DDL** **§12**, **§16**, **2.89** / **2.90**; **LLD** **§2.4e**.
- **Order:** When every line is **Ready**, one **`prepare_order`** / **`_create_order`** call attaches **all** vehicles in the batch (multi-line **`order_line_vehicles`**) using the **add_subdealer_challan** Siebel profile (**BR-22**). Master **`invoice_status`** tracks order/invoice outcome; **invoice complete** when an **invoice number** is scraped as agreed in product rules.
- **Commit:** **`challan_master`** holds order/invoice references and totals; **`challan_details`** links to **`vehicle_inventory_master`** lines. Detail staging rows move to **Committed** after successful commit.

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
- Submit Info persists a **draft** **`add_sales_staging`** row only (**`POST /submit-info`**, **`staging_id`**); **Create Invoice** uses **`payload_json`** via **`staging_id`** then commits masters; **Generate Insurance** uses committed IDs + **`staging_id`** (**FR-17**, **LLD §2.2a**).
- Fill DMS scrapes vehicle data; after **Invoice#** and master commit, may download default **Run Report** GST PDFs (**BR-21**); always writes `DMS_Form_Values.txt` (and per-run `Playwright_DMS_*.txt` where configured) under `ocr_output`.
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
| 1.10 | Mar 2026 | — | **`form_insurance_view`**: master-backed fields only; proposal defaults (email, add-ons, payment, etc.) hardcoded in Playwright |
| 2.0 | Mar 2026 | — | FR-23: Aadhaar OCR fallbacks — **DOB-anchored gender** (skip token, `/`, gender) and **comma + dash-run** state/PIN before last PIN |
| 2.1 | Mar 2026 | — | FR-18: **DMS_MODE** / **DMS_REAL_URL_*** for Hero Connect Siebel vs dummy HTML; settings API exposes mode |
| 2.2 | Mar 2026 | — | **§6.1a** Hero Connect / Siebel target DMS automation checklist (ordered); **§6.8** reconciliation pointer to LLD §2.4d; **FR-18** cross-reference |
| 2.3 | Mar 2026 | — | **FR-18** updated for real Siebel §6.1a implementation (`skip_find` + default Find branch + vehicle split) |
| 2.4 | Mar 2026 | — | **§6.1a** step 4a: **Pre Check** before PDI on In Transit branch |
| 2.5 | Mar 2026 | — | **FR-23** scans-v2: QR+Textract order, parallel Aadhaar+Details, Textract name/geo fallbacks, `section_timings_ms` |
| 2.6 | Mar 2026 | — | **FR-23** / **BR-17**: Aadhaar printed text uses **AWS Textract only** (no Tesseract on Aadhaar front/back) |
| 2.7 | Mar 2026 | — | **FR-23** / **BR-17**: scans-v2 and `get_extracted_details` drop **UIDAI QR**; Aadhaar is **Textract + Raw_OCR parsers** only; **`section_timings_ms`** removes QR keys, adds **`aws_textract_prefetch_ms`** |
| 2.8 | Mar 2026 | — | **§6.1a** step **2a**: when Contact Find has **no table match**, preferred path is **vehicle VIN/chassis + engine** then **Enquiry** / **Opportunities List:New** with DB-only fields (see **LLD §2.4d** **`_add_enquiry_opportunity`**) before fallback basic enquiry form |
| 2.9 | Mar 2026 | — | **§6.1a** step **2a**: clarified Hero Connect flow — **Find → Vehicles**, right fly-in **VIN** / **Engine#** with `*` wildcards + **Enter**; model/year/color from grid and/or **Vehicle Information** (**Dispatch Year**) |
| 3.0 | Mar 2026 | — | **§6.1a** step **2a**: Add Enquiry opportunity form — expanded DB/scrape fields; **Financier** not automated; API error text includes concrete **`_add_enquiry_opportunity`** failure detail |
| 3.1 | Mar 2026 | — | **BR-16** updated: Create Invoice now automated; **DMS scrape persistence**: all scraped values (Enquiry#, Order#, Invoice#, vehicle_ex_showroom_cost, cubic_capacity, vehicle_type) persisted to DB; `update_sales_master_from_dms_scrape` runs for real Siebel path |
| 3.2 | Mar 2026 | — | Fill DMS service renamed to **`fill_hero_dms_service.py`**; execution guarded by **`dealer_ref.oem_id`** (Hero = `1`; otherwise error: **"Currently only Hero MotoCorp Limited is  configured as OEM"**) |
| 3.3 | Mar 2026 | — | Added **§6.1b** normative rules for real Siebel Contact Find/Add Enquiry: same-frame selectors (**Mobile** `title="Mobile Phone"`, **First Name** `id="field_textbox_1"`), exact mobile+first matching, duplicate open-enquiry selection order, dot-suffix create path, and Enquiry# timed save gate (0.5s/2.5s/3.5s) with hard-fail logging |
| 3.4 | Mar 2026 | — | **§6.1b** updated with strict Find→Vehicles selector rule: fill **VIN** `id="field_textbox_0"` and **Engine#** `id="field_textbox_2"` in the same frame; fail if unavailable |
| 3.5 | Mar 2026 | — | **§6.1b** / **BR-19**: grid row first-name match clarified — trimmed **case-insensitive** equality; implementation may use row **textContent**, cell **input/textarea** values, and **title/aria-label** when Siebel omits names from visible **innerText** |
| 3.6 | Mar 2026 | — | **§6.1b**: first-name row match extended for **compound** Siebel display (**“Lavesh Faujdar”** vs Find **“Lavesh”** / **“Lavesh.”**) via first-token and prefix rules consistent with Hero CRM |
| 3.7 | Mar 2026 | — | **§6.1b**: Contact Find First Name field uses **starts-with** query (**`<first>*`**) so compound names in Siebel match the sheet’s single-token first name |
| 3.8 | Mar 2026 | — | **§6.1b**: **open enquiry** detection on **Contact_Enquiry** reads Enquiry# from **`name="Enquiry_"`** fields (and related table scrape), plus frame aggregation for duplicate-mobile sweep |
| 3.9 | Mar 2026 | — | **§6.1b**: duplicate-mobile sweep — **same-frame** multi-row list: drill **each** row, **Contact_Enquiry** per open; subgrid eval order **main document first** then iframes (**LLD** **6.10**) |
| 3.10 | Mar 2026 | — | **§6.1b**: duplicate-mobile sweep — **in-place** drills only (**no** Contact Find between rows); on failure, sweep stops; ordinal ≥1 uses **mobile-only** row match (**LLD** **6.12**) |
| 3.11 | Mar 2026 | — | Insurance automation decoupled from DMS: shared browser/tab logic in **`handle_browser_opening`**; insurance DB/OCR helpers in **`insurance_form_values`** / **`insurance_kyc_payloads`**; **`run_fill_insurance_only`** lives in **`fill_hero_insurance_service`**; **`Playwright_insurance.txt`** trace under `ocr_output/<dealer>/<subfolder>/` |
| 3.12 | Mar 2026 | — | **§6.1b**: Contact Find **First Name** is **exact** (no **`*`** wildcard); grid match requires **mobile + exact first name** (case-insensitive equality on a cell); superseded fuzzy / starts-with / first-token row rules for Find + primary grid hit (**LLD** **6.18**) |
| 3.13 | Mar 2026 | — | **Create booking** (Vehicle Sales new order): **Comments** field set to **`Battery is <Battery No>`** when detail sheet / DMS fill battery is present (**LLD** **6.19**) |
| 3.14 | Mar 2026 | — | Vehicle Sales **Create Order**: before **+**, **Find** → **Mobile Phone#** → mobile query; if a matching order **row** exists, skip new booking and run **`attach_vehicle_to_bkg`** only (**LLD** **6.20**) |
| 3.15 | Mar 2026 | — | **§6.1b** / **BR-19**: reverted **strict exact** grid + drilldown row matching (**3.12**); **Find** First Name stays **exact** (no `*`); **`_siebel_ui_suggests_contact_match_mobile_first`** and **`_contact_mobile_drilldown_plans`** restored to **fuzzy** first-name + **mobile-only** fallback (**LLD** **6.21**) |
| 3.16 | Mar 2026 | — | **FR-8** / **FR-17**: target **Add Sales staging** (`add_sales_staging`, **`staging_id`**); commit-after-Create-Invoice; **LLD §2.2a** |
| 3.17 | Mar 2026 | — | **BR-9**, **BR-13**, **FR-18**, **§6.1a** / **§6.2**: DMS fill without **`form_dms_view`** — OCR staging + **`form_dms.py`** inline join (**`13b_drop_form_dms_view.sql`**) |
| 3.18 | Mar 2026 | — | **BR-9**: Create Invoice with **`staging_id`** — DMS inputs from **`add_sales_staging.payload_json`** + scrape only; **`POST /fill-dms`**, **`/fill-dms/dms`** |
| 3.19 | Mar 2026 | — | **BR-20** (superseded by **3.20**): earlier draft referred to a minimal **`insurance_master` seed**; product choice is now view + staging merge |
| 3.20 | Mar 2026 | — | **BR-20**: Generate Insurance inputs — **`form_insurance_view`** + **`add_sales_staging.payload_json`** via **`staging_id`**; **FR-18b** updated; **§6.2** |
| 3.21 | Mar 2026 | — | **BR-20** / **FR-8** / **FR-18b** / **§6.2**: **`form_insurance_view`** + staging **`payload_json`** are the **joint** complete input set; Add Sales passes **`staging_id`** for **Generate Insurance** as well as Create Invoice |
| 3.22 | Mar 2026 | — | **BR-20** / **FR-18b** / **§6.4** / **§6.5**: **`insurance_master`** **INSERT** only for **`(customer_id, vehicle_id, insurance_year)`** (fail on duplicate); **Issue Policy** click + second scrape updates **`policy_num`** / **`insurance_cost`** — **`insert_insurance_master_after_gi`**, **`update_insurance_master_policy_after_issue`** |
| 3.23 | Mar 2026 | — | **FR-18**: removed static DMS/Vaahan/insurance training sites; **`DMS_MODE`** default **real**; Vaahan/RTO Pay Playwright stubs until production — **LLD §2.4a–c** |
| 3.24 | Mar 2026 | — | **§6.1a** step **4a** / **`prepare_vehicle`**: in-transit branch is **receipt / Process Receipt** only; **no** automated Pre-check/PDI URL flow while in-transit; **dealer** path runs **single** tab Pre-check/PDI (**LLD** **6.48**); URL helper code removed (**LLD** **6.49**) |
| 3.25 | Mar 2026 | — | **§6.1a** / **`prepare_vehicle`**: Auto Vehicle List search is **only** Find→Vehicles **`*`**VIN + **`*`**Engine partials (**LLD** **6.51**). Vehicle Sales **Create Order** no longer runs **Find** → **Mobile Phone#** before **+** to skip duplicate bookings (**supersedes** changelog **3.14** / **LLD** **6.20**) |
| 3.26 | Mar 2026 | — | **§6.1a**: on vehicle serial detail, **Pre-check** and **PDI** tabs are selected from **Third Level View Bar** when present (with legacy fallbacks). Vehicle merge/persist normalizes **Year of Mfg** to strict **`YYYY`** (e.g., `2,024` → `2024`) |
| 3.27 | Mar 2026 | — | OCR address normalization removes a stray 6-digit PIN that appears immediately before state token (for example before **Rajasthan**) so persisted **Address Line 1** does not include duplicate pin fragments |
| 3.28 | Mar 2026 | — | **§6.1a** step **3** / **`prepare_vehicle`**: ordered vehicle prep aligned with Siebel navigation — mandatory left-pane **Search Results** **Title** drill-in when chassis is known; **Key/Battery** and aria detail **before** **Inventory Location** gate; **dealer** stock → **Serial Number** drilldown ( **`#gview_s_1_l`** ) → **Features** → **Pre-check** → **PDI** (**LLD** **6.72**); no DB/schema change (**Database DDL** **2.13**) |
| 3.29 | Mar 2026 | — | **§6.1a** step **3**: if the list shows **one** hit whose Title displays the **full VIN** but automation only has a **partial** (or not yet **`full_chassis`**), **`_siebel_try_click_vin_search_hit_link`** may open that row via the **single** VIN-like Title drilldown (**LLD** **6.74**) |
| 3.30 | Mar 2026 | — | **§6.1a** / dealer vehicle prep: if **Features** HHML fields are already visible after navigation, do **not** repeat top-grid **VIN** drilldown; scrape **cubic**/**vehicle_type** and continue **Pre-check**/**PDI** (**LLD** **6.75**) |
| 3.31 | Mar 2026 | — | **§6.1a** / **Features** recognition: treat visible **Features in Vehicles** (**aria-label**) as the Features step; activate **Features & Image** via **`#s_vctrl_div`** when role-based tab locators miss (**LLD** **6.76**) |
| 3.32 | Mar 2026 | — | **§6.1a** / **dealer** **prepare_vehicle**: after **Serial Number** drilldown, read **cubic**/**vehicle_type** from the **Features & Image** view **without** a separate Features tab click (**LLD** **6.77**) |
| 3.33 | Mar 2026 | — | **§6.1a** / **Features** grid: when values are in **`summary="Features"`** (e.g. **CC Category**, **Class of Vehicle**), scrape those rows for **cubic**/**vehicle_type** (**LLD** **6.78**) |
| 3.34 | Mar 2026 | — | **§6.1a** / dealer **prepare_vehicle**: if HHML cells expose data in cell **`title`** (e.g. `4_s_1_l_HHML_Feature_Value` = `125 CC`), scrape via explicit id fallback and continue to **Pre-check/PDI**; focus shift after scrape attempt is expected because workflow proceeds to next mandatory step (**LLD** **6.79**) |
| 3.35 | Mar 2026 | — | **§6.1a** / operator diagnostics: **`[frame-focus]`** JSON snapshots on **`prepare_vehicle`** / serial-detail **note** trail to trace iframe and **`document.activeElement`** across Serial → Features scrape → **Pre-check**/**PDI** (**LLD** **6.80**) |
| 3.36 | Mar 2026 | — | **§6.1a** rollback baseline: restore historical serial-detail order from commit **`ab903064`** where **Pre-check/PDI** runs immediately after **Serial Number** (with early HHML id scrape), then **Features & Image** tab scrape; used as regression-safe base to rebuild incremental fixes (**LLD** **6.81**) |
| 3.37 | Mar 2026 | — | **§6.1a**: **`cubic_capacity`** from Siebel Features / HHML scrape is stored as **digits only** (e.g. **`125`**), not suffixed text like **`125 cc`** (**LLD** **6.82**) |
| 3.38 | Mar 2026 | — | Payments path: use **Payments tab** activation first, then root/query flow; if Save icon fails after values are filled, attempt **Ctrl+S** and only treat success as valid when **Transaction#** is populated (**LLD** **6.83**) |
| 3.39 | Mar 2026 | — | Payments save behavior: after entering amount, use **Ctrl+S first**; Save icon click is fallback only; save is considered successful only when **Transaction#** is populated (**LLD** **6.84**) |
| 3.40 | Mar 2026 | — | **§6.1a** / video SOP: optional temporary automation gate — after **Payments**, **Generate Booking** and **create order** may be skipped when **`SIEBEL_DMS_HARD_FAIL_BEFORE_BOOKING_AND_ORDER`** is True; API surfaces **`out["error"]`** (**LLD** **6.90**). **Superseded:** gate removed — **LLD** **6.91**. |
| 3.41 | Mar 2026 | — | **§6.1a** / Fill DMS: logging-only cleanup (trial JSON / diag notes per **LLD** **6.91**). **Superseded:** temporary stop after payments removed — **BRD** **3.48** / **LLD** **6.116**. |
| 3.42 | Mar 2026 | — | **§6.1a** step **2a** / **no contact match**: **`_add_enquiry_opportunity`** may skip redundant vehicle grid scrape when **`prepare_vehicle`** already merged model/year/color — **LLD** **6.93**; full vehicle find + VIN drill remain — **LLD** **6.95**. |
| 3.43 | Mar 2026 | — | **§6.1a** step **2a**: Add Enquiry skipped **Auto Vehicle List** when merge complete — **LLD** **6.94** (**superseded by** **3.44** / **LLD** **6.95**). |
| 3.44 | Mar 2026 | — | **§6.1a** step **2a**: Add Enquiry **always** runs Find→Vehicles + VIN drill; when **`prepare_vehicle`** merge is complete, **only** the duplicate post-drill scrape is skipped — **LLD** **6.95** |
| 3.45 | Mar 2026 | — | **§6.1a** Contact Find: bounded waits after Find/Go and after left-pane drill (strategy 1) + mobile-only Find first with mobile+first fallback (strategy 2) — **LLD** **6.96** |
| 3.46 | Mar 2026 | — | **§6.1a** **`Playwright_DMS.txt`** **`[TRACE:FC→FN:*]`** timestamps for Find → first-name drill / relation care-of — **LLD** **6.97** |
| 3.47 | Apr 2026 | — | **§6.1a** step **4b** / **`_create_order`**: restored **My Orders** mobile search before **+** with grid branching (**invoiced** / **pending** / **allocated** / new booking); supersedes **3.25** removal of that search — **LLD** **6.115** |
| 3.48 | Apr 2026 | — | **§6.1a** / video SOP: removed temporary stop after **Payments** (**`SIEBEL_DMS_HARD_FAIL_BEFORE_BOOKING_AND_ORDER`**); Siebel **Create Invoice** auto-click enabled in **`_attach_vehicle_to_bkg`**; **Playwright_DMS** / insurance trace timestamps in **IST** — **LLD** **6.116** |
| 3.47 | Mar 2026 | — | **§6.1a** optional **Mobile Search Results** iframe hint (**DMS_SIEBEL_MOBILE_SEARCH_HIT_ROOT_HINT_***) — **LLD** **6.98** |
| 3.48 | Mar 2026 | — | **§6.1a** **`Playwright_DMS.txt`** optional **`title_drilldown_trial_hint_json=`** / **`contact_enquiry_subgrid_trial_hint_json=`** lines after successful runs (for future hardcoding); automation behavior unchanged — **LLD** **6.99** |
| 3.49 | Mar 2026 | — | **§6.1a** post–drill contact readiness may match **Contacts** first-name targets (same family as open-record drill); video path **``prepare_vehicle``** remains before Contact Find (navigation) — **LLD** **6.100** |
| 3.50 | Mar 2026 | — | **§6.1a** default Siebel iframe priority uses **hard-coded** Hero **SWEView** / applet fragments (Contact Find Search Results, title drilldown, Contact_Enquiry subgrid); optional env override — **LLD** **6.101** |
| 3.51 | Mar 2026 | — | **§6.1a** Contact Find mobile/title grid: resolve hinted **Frame** directly when URL matches builtin; full iframe sweep only if no rows — **LLD** **6.103** |
| 3.52 | Mar 2026 | — | **§6.1a** **`Playwright_DMS.txt`**: removed temporary **`[TRACE:FC→FN:*]`** and trial hint **`note`** JSON lines (Contact Find / drill / enquiry paths); SOP behavior unchanged — **LLD** **6.104** |
| 3.53 | Mar 2026 | — | **§6.1a** / **`Playwright_Hero_DMS_fill`**: removed former **linear** staged chain and **`SIEBEL_DMS_STOP_AFTER_ALL_ENQUIRIES`**; **Find Contact Enquiry** path is the only Fill DMS implementation — **LLD** **6.105**, **Database DDL** **2.46** |
| 3.54 | Mar 2026 | — | **§6.1a**: removed unused **`_fill_basic_enquiry_details`**, **`_refind_customer_after_enquiry`**, **`_fill_siebel_enquiry_customer_applet`**, **`_fill_siebel_care_of_only`** — **LLD** **6.106**, **Database DDL** **2.47** |
| 3.55 | Mar 2026 | — | **§6.1a** / **`_attach_vehicle_to_bkg`**: post–**Allocate All** vehicle serial Pre-check/PDI automation disabled (`if False`) — **LLD** **6.107**, **Database DDL** **2.48** |
| 3.56 | Mar 2026 | — | **§6.1a** / video SOP: after **Add customer payment**, Fill DMS result includes **`dms_customer_master_collated`** (proposed **`customer_master`** fields + mapping notes) — **LLD** **6.108**, **HLD** **1.44**, **Database DDL** **2.49** (**pre-booking hard-fail gate removed** — **BRD** **3.48**) |
| 3.57 | Mar 2026 | — | **`dms_customer_master_collated`**: profession / marital / aadhar documented as **detail sheet**; **`customer_id`** system-generated; **`dms_relation_prefix`** from address first three characters; payments not called out — **LLD** **6.109**, **HLD** **1.45**, **Database DDL** **2.50** |
| 3.58 | Mar 2026 | — | Post-**create_order**: ex-showroom from attach scrape; **`sales_master`** order/invoice prep + single DB transaction with **`customer_master`** collate / **`vehicle_master`** / **`sales_master`** — **LLD** **6.110**, **HLD** **1.46**, **Database DDL** **2.51** |
| 3.59 | Mar 2026 | — | Fill DMS with **no** prior **`customer_id`**/**`vehicle_id`**: **INSERT** all three masters after **`create_order`**; otherwise **UPDATE** path — **LLD** **6.111**, **HLD** **1.47**, **Database DDL** **2.52** |
| 3.60 | Mar 2026 | — | On Siebel **INSERT** path, **`file_location`** stored on **`customer_master`** / **`sales_master`** is the per-sale OCR folder **`ocr_output/{dealer_id}/{mobile}_{ddmmyyyy}`** (aligned with Add Sales / pre-OCR layout) — **LLD** **6.112**, **HLD** **1.48**, **Database DDL** **2.53** |
| 3.61 | Mar 2026 | — | **Submit Info:** one DB commit to **`add_sales_staging`** only. **No** writes to **`customer_master`**/**`vehicle_master`**/**`sales_master`** during Siebel until **Create Invoice** is reflected by a scraped **Invoice#**; then a single INSERT batch (video: **`insert_dms_masters_from_siebel_scrape`**; staging: **`commit_staging_masters_and_finalize_row`** with merged scrape). In-run logging via timestamped **`Playwright_DMS_*.txt`** only — **LLD** **6.113**, **6.117**, **HLD** **1.49**, **1.53**, **Database DDL** **2.54**, **2.58** |
| 3.62 | Mar 2026 | — | **Add Sales (client):** when **`oem_id`** = Hero (**`1`**) and financier text **starts with** “Bajaj” (case-insensitive), **Submit Info** sends **`customer.financier`** = **“Hinduja”** to staging; UI shows a note under Financier that systems will log **Hinduja** — **HLD** **1.50**, **LLD** **6.114**, **Database DDL** **2.55** |
| 3.63 | Apr 2026 | — | **§6.1a** / Fill DMS: Siebel operator trace is a **new** file each run — **`Playwright_DMS_<ddmmyyyy>_<hhmmss>.txt`** (IST) in the OCR subfolder — **LLD** **6.117**, **HLD** **1.53**, **Database DDL** **2.58** |
| 3.64 | Apr 2026 | — | **§6.1a** **`_create_order`** / **My Orders**: classify grid rows from full row text; **Allocated** path when Order# present without Invoice# even if prior outcome was **unknown_rows** — **LLD** **6.118**, **HLD** **1.54**, **Database DDL** **2.59** |
| 3.65 | Apr 2026 | — | **§6.1a** **My Orders** multi-row: **allocated** classification before **pending** so **Order#** drill targets **Allocated** row — **LLD** **6.119**, **HLD** **1.55**, **Database DDL** **2.60** |
| 3.66 | Apr 2026 | — | **FR-8** / Add Sales: **`create-invoice-eligibility`** returns resolved **`customer_id`** / **`vehicle_id`**; client syncs after Create Invoice so **Generate Insurance** enables when invoice is recorded — **LLD** **6.120**, **HLD** **1.56**, **Database DDL** **2.61** |
| 3.67 | Apr 2026 | — | **FR-18b** Hero MISP **Generate Insurance**: landing **Login** and **2W** entry selectors broadened — **LLD** **6.121**, **HLD** **1.57** |
| 3.68 | Apr 2026 | — | **FR-18b** **2W** tile **`aid="ctl00_TWO"`** — **LLD** **6.122**, **HLD** **1.58** |
| 3.69 | Apr 2026 | — | **FR-18b** MISP login **`button[type="submit"]`** **Sign In** — **LLD** **6.123**, **HLD** **1.59** |
| 3.70 | Apr 2026 | — | **FR-17** / **§6.1a** trace: **`Playwright_DMS_*.txt`** appends committed three-master JSON after Create Invoice DB write — **LLD** **6.124**, **HLD** **1.60** |
| 3.71 | Apr 2026 | — | **Hero Insurance** **pre_process**: **`Playwright_insurance.txt`** **`[DIAG]`** lines list URL, frames, and visible login-related controls when Sign In is attempted; automation tries **iframe** document trees for the Sign In control — **LLD** **6.125**, **HLD** **1.61** |
| 3.72 | Apr 2026 | — | **Hero Insurance**: **`[DIAG]`** includes **`#root`**-only control list; Sign In tries **`#root`** scope first; if **subfolder** is not sent on **`POST /fill-dms/insurance/hero`**, diagnostics append to dealer **`Playwright_insurance_diag_fallback.txt`** — **LLD** **6.126**, **HLD** **1.62** |
| 3.73 | Apr 2026 | — | **Hero Insurance** single API run: **`main_process`** reuses the same browser tab as **`pre_process`** (no second managed window when CDP tab matching would miss) — **LLD** **6.127**, **HLD** **1.63** |
| 3.74 | Apr 2026 | — | **Fill Insurance** (**`POST /fill-dms/insurance`**) attempts automated **Sign In** and writes **`[DIAG]`** before the KYC wait (same helpers as Hero landing) — **LLD** **6.128**, **HLD** **1.64** |
| 3.75 | Apr 2026 | — | **Hero / MISP partner login**: automated click targets **Sign In** inside the password form (not hero **Get Price** submit) — **LLD** **6.129**, **HLD** **1.65** |
| 3.76 | Apr 2026 | — | **MISP partner**: wait for **non-empty password** (Partner Login), then **Sign In**; DOM submit in same **form** — **LLD** **6.130**, **HLD** **1.66** |
| 3.77 | Apr 2026 | — | **MISP**: up to **4** Sign In tries (**500 ms**); success when URL leaves **misp-partner-login** — **LLD** **6.131**, **HLD** **1.67** |
| 3.78 | Apr 2026 | — | **MISP partner login**: **`requestSubmit`** on password form before clicks; post-submit UI hints may be logged when still on partner URL — **LLD** **6.132**, **HLD** **1.68** |
| 3.79 | Apr 2026 | — | **FR-18b** / shared browser: **`get_or_open_site_page`** avoids a **second** insurance (or launcher) tab when CDP attaches before navigation; **`www.`** host normalization for tab match — **LLD** **6.133**, **HLD** **1.69** |
| 3.80 | Apr 2026 | — | **Fill Insurance** (**`POST /fill-dms/insurance`**): after login, **2W** + **New Policy** like Hero landing; KYC wait does not force site root if already authenticated — **LLD** **6.134**, **HLD** **1.70** |
| 3.81 | Apr 2026 | — | **MISP**: **2W** / **New Policy** may open a **new** tab; automation attaches to that tab before KYC — **LLD** **6.135**, **HLD** **1.71** |
| 3.82 | Apr 2026 | — | **FR-18b**: With DMS + Insurance in one browser, automation must not attach to Siebel/DMS tabs for insurance steps — **LLD** **6.136**, **HLD** **1.72** |
| 3.83 | Apr 2026 | — | **FR-18b** MISP nav: expand **Policy Issuance** before **New Policy** — **LLD** **6.137**, **HLD** **1.73** |
| 3.84 | Apr 2026 | — | **§6.6** KYC **Insurance Company**: typeahead uses **`fuzzy_best_option_label`** on visible options (no first-option fallback when the match score is below threshold) — **LLD** **6.138**, **HLD** **1.74** |
| 3.85 | Apr 2026 | — | **FR-18b** Fill Insurance: automation must not attach to or click **Login** on a Siebel/DMS tab when both DMS and MISP share one browser — **LLD** **6.139**, **HLD** **1.75** |
| 3.86 | Apr 2026 | — | **FR-18b** **`Playwright_insurance.txt`** DIAG: compact control snapshot by default (optional verbose via **`INSURANCE_DIAG_FULL_CONTROL_SNAPSHOT`**) — **LLD** **6.140**, **HLD** **1.76** |
| 3.87 | Apr 2026 | — | **§6.6** KYC **Insurance Company** on **`ekycpage`**: label + full **`<select>`** scan, **`force`** on hidden native select, broader custom-list matching — **LLD** **6.141**, **HLD** **1.77** |
| 3.88 | Apr 2026 | — | **§6.6** **`ekycpage`**: optional keyboard SOP (Tab/type/ArrowDown insurer → OVD → mobile → consent) — **LLD** **6.142**, **HLD** **1.78** |
| 3.89 | Apr 2026 | — | **§6.6** MISP KYC keyboard SOP applies on the same URL patterns as the KYC wait (**`kycpage.aspx`**, **`/ekyc`**, etc.); framed KYC clicks the **iframe** before the Tab chain — **LLD** **6.143**, **HLD** **1.79** |
| 3.90 | Apr 2026 | — | **§6.6** KYC: if keyboard cannot focus OVD after insurer, OVD/mobile/consent are filled in the **KYC frame** via DOM — **LLD** **6.144**, **HLD** **1.80** |
| 3.91 | Apr 2026 | — | **§6.6** KYC keyboard: insurer/mobile clicks + **`Control+A`** only when the focused element is editable (avoids selecting all page text) — **LLD** **6.145**, **HLD** **1.81** |
| 3.92 | Apr 2026 | — | **§6.6** Optional **`kyc_nav_scrape`** **`[DIAG]`** on KYC (visible controls + page metrics per frame) before automation — **LLD** **6.146**, **HLD** **1.82** |
| 3.93 | Apr 2026 | — | **§6.6** **`kyc_nav_scrape`** includes **`all_selects`** (every **`<select>`**, including hidden) for **ContentPlaceHolder1** / OVD mapping — **LLD** **6.147**, **HLD** **1.83** |
| 3.94 | Apr 2026 | — | **§6.6** **`kyc_nav_scrape_after_insurer`** after insurer commit (Tab blur + scrape) — **LLD** **6.148**, **HLD** **1.84** |
| 3.95 | Apr 2026 | — | **§6.6** KYC insurer fuzzy matching (typos vs portal labels) — **LLD** **6.149**, **HLD** **1.85** |
| 3.96 | Apr 2026 | — | **§6.6** KYC DIAG after **`networkidle`** + after **KYC Partner** select — **LLD** **6.150**, **HLD** **1.86** |
| 3.97 | Apr 2026 | — | **`dealer_ref.prefer_insurer`**, **`form_insurance_view.prefer_insurer`**, **`build_insurance_fill_values`** replaces merged insurer with **`prefer_insurer`** when fuzzy similarity ≥20% — **LLD** **6.151**, **HLD** **1.87**, **Database DDL** **2.62** |
| 3.98 | Apr 2026 | — | **§6.6** MISP KYC: blur insurer (**Tab**) after commit even without DIAG **`subfolder`**; **KYC Partner** not changed by automation (portal default) — **LLD** **6.152**, **HLD** **1.88** |
| 3.99 | Apr 2026 | — | **§6.6** MISP KYC: consent checkbox before **Proceed** / **KYC Verification**; already-verified AADHAAR banner → skip fill and click **Proceed** / policy issuance — **LLD** **6.153**, **HLD** **1.89** |
| 3.100 | Apr 2026 | — | **§6.6** MISP KYC: two branches — verified AADHAAR + policy issuance **banner** (match full portal copy → consent + **Proceed**); **no** banner → three document **file** inputs then consent + **Proceed** (`_kyc_proceed_or_upload`) — **LLD** **6.154**, **HLD** **1.90** |
| 3.101 | Apr 2026 | — | **§6.6** MISP KYC: verified statement and **Proceed** after **mobile** entry (not before); **`_kyc_post_mobile_entry_branch`** — **LLD** **6.155**, **HLD** **1.91** |
| 3.102 | Apr 2026 | — | **§6.6** MISP KYC keyboard: **`Escape`** after insurer commit; **OVD** = **AADHAAR CARD** via DOM in KYC frame before Tab+ArrowDown; restore **KYC Partner** to **`KYC_DEFAULT_KYC_PARTNER_LABEL`** after OVD if focus/ArrowDown moved it — **LLD** **6.156**, **HLD** **1.92** |
| 3.103 | Apr 2026 | — | **§6.6** MISP KYC: blur insurer **`ddlproduct`** when focus stuck; **mobile** via locator **`fill`** before OVD→mobile **Tab** count; do not **`keyboard.type`** mobile into **`<select>`** — **LLD** **6.157**, **HLD** **1.93** |
| 3.104 | Apr 2026 | — | **§6.6** / **FR-18b**: after KYC **Proceed**, VIN/Chassis in **`txtFrameNo`** and **Submit** via **`btnSubmit`**; KYC insurer **Escape** then **`_kyc_tab_out_of_insurer_after_escape`** (**2× Tab**) — **LLD** **6.158**–**6.159**, **HLD** **1.94** |
| 3.105 | Apr 2026 | — | **§6.6** / **FR-18b**: VIN fields under **`upnlAddStateMaster`** / **`mainContainer`** (frames + **`force`** fill) — **LLD** **6.160**, **HLD** **1.95** |
| 3.106 | Apr 2026 | — | **§6.6**: **`txtFrameNo`** is VIN only (**`divtxtFrameNo`**); KYC mobile wait must not use it — **LLD** **6.161**, **HLD** **1.96** |
| 3.107 | Apr 2026 | — | **§6.6** / **FR-18b**: after KYC **Proceed**, automation waits until the real VIN/Chassis control is present (brief no-action intermediate page is tolerated) — **LLD** **6.165**, **HLD** **1.99** |
| 3.108 | Apr 2026 | — | **§6.6** / **FR-18b**: MISP KYC — **`Enter`** after insurer commit (**`_hero_insurance_kyc_nav_after_insurer_commit`**, **`_kyc_tab_out_of_insurer_after_escape`**); VIN sweep prefers **2W** app frames over stale **KYC** frames; default **`iframe[src*="2w" i]`** — **LLD** **6.166**, **HLD** **1.100** |
| 3.109 | Apr 2026 | — | **Add Sales** extracted customer **Date of birth** field: compact width (not full **`<dd>`** stretch) — **LLD** **6.166**, **HLD** **1.101** |
| 3.110 | Apr 2026 | — | **§6.6** / **FR-18b**: **`Playwright_insurance.txt`** logs **`vin_transition`** when navigation moves from intermediate MISP screens to **`MispDms.aspx`** (path-based; dynamic query not echoed) — **LLD** **6.167**, **HLD** **1.102** |
| 3.111 | Apr 2026 | — | **§6.6** / **FR-18b**: **`main_process`** VIN step uses an extended timeout and **`wait_for_url`** **`MispDms.aspx`** after KYC **Proceed**; **Please wait** overlay on **`ekycpage.aspx`** logged — **LLD** **6.169**, **HLD** **1.104** |
| 3.112 | Apr 2026 | — | **FR-18b** / **BR-20**: API prefix **`/fill-forms`** (router **`fill_forms_router`**); Create Invoice **`POST /fill-forms`**, **`POST /fill-forms/dms`**; **Generate Insurance** calls **`POST /fill-forms/insurance/hero`** only; **`pre_process`** delegates to **`run_fill_insurance_only`** (removed **`POST /fill-dms/insurance`**) — **LLD** **6.171**, **HLD** **1.106** |
| 3.113 | Apr 2026 | — | **FR-18b**: On real MISP, **`pre_process`** completes **VIN** field + VIN page **Submit**; **`main_process`** handles **I agree** (if shown) and proposal onward — **LLD** **6.172**, **HLD** **1.107** |
| 3.114 | Apr 2026 | — | **FR-18b**: Post–VIN **Submit** modal — automation targets **`button#btnOK`** (**I Agree**) in **`div.modal-content`** before generic **I agree** discovery — **LLD** **6.173** |
| 3.115 | Apr 2026 | — | **FR-18b**: Bundled training HTML (**`#ins-company`**) flow **disabled**; **`INSURANCE_BASE_URL`** must be real MISP — **LLD** **6.174**; **HLD** **1.109** |
| 3.116 | Apr 2026 | — | **FR-18b**: **`Playwright_insurance.txt`** — removed **`login_page_snapshot`** / **`kyc_nav_scrape`** full-page visible-control dumps; debug-only page context — **LLD** **6.177**, **HLD** **1.110** |
| 3.117 | Apr 2026 | — | **FR-18b**: Optional pause of **Proposal Review** / **Issue Policy** automation (**`HERO_MISP_PAUSE_PROPOSAL_REVIEW_AND_ISSUE_POLICY`**) until form fill reviewed — **LLD** **6.178**, **HLD** **1.111** |
| 3.118 | Apr 2026 | — | **§6.4** / **§6.6**: **`dealer_ref.hero_cpi`** (**Y**/**N**) on **`form_insurance_view`** drives MISP CPA NIC/CPI add-on check/uncheck — **LLD** **6.196**, **HLD** **1.117**, **Database DDL** **2.63** |
| 3.119 | Apr 2026 | — | **FR-18b** / **§6.4** / **§6.5**: preview scrape **`policy_num`**, **`policy_from`**, **`policy_to`**, **`premium`**, **`idv`**; **`insurance_cost`** column dropped — **`DDL/alter/14c`**; **`update_insurance_master_policy_after_issue`** takes full scrape dict — **LLD** **6.200** |
| 3.120 | Apr 2026 | — | **FR-18b** / **BR-20**: MISP proposal automation aligns **DOB** (normalized **dd/mm/yyyy** + staging **`customer`/`insurance`**), **nominee** (staging **`customer.nominee_name`** when view empty), **CPA tenure 0**, **USGI** uncheck, RTI/RSA/Emergency add-ons, **Hero CPI** row, **HDFC** payment — **LLD** **6.201**, **HLD** **1.120** |
| 3.121 | Apr 2026 | — | **FR-18b**: **pre_process** targets lower wall time — post-mobile pre-Proceed **`domcontentloaded`** cap, **2W**/**New Policy** landing cap, VIN preamble **`domcontentloaded`** cap; optional **`.env`** — **LLD** **6.202**, **HLD** **1.123** |
| 3.122 | Apr 2026 | — | **FR-18b**: **main_process** proposal uses MispPolicy **CPH1** checkbox ids (add-ons, USGI) where available; DOB commit + reassert; nominee force-fill — **LLD** **6.203**, **HLD** **1.124** |
| 3.123 | Apr 2026 | — | **FR-18b**: **main_process** proposal targets **main document first** (**`purpose="proposal"`**); conditional DOB reassert when **`txtDOB`** readback mismatches; nominee gender **`force`** check — **LLD** **6.204**, **HLD** **1.125** |
| 3.124 | Apr 2026 | — | **FR-18b**: MISP **pre_process** — KYC eKYC insurer **strategy cache** (per portal host), **`fuzzy_scan`** last; **VIN** **`Playwright_insurance.txt`** phase **`NOTE`** lines + shorter waits (**`INSURANCE_VIN_*`**, **`HERO_MISP_LANDING_WAIT_MS`**) — **LLD** **6.205**, **HLD** **1.126** |
| 3.125 | Apr 2026 | — | **FR-18b**: **main_process** proposal — **`txtNomineeAge`** commit/readback hardening (sequential type, blur events, DOM readback fallback) so later steps (add-ons, CPA, HDFC, USGI) run — **LLD** **6.206**, **HLD** **1.127** |
| 3.126 | Apr 2026 | — | **FR-18b**: Hero Insurance **`_t`** / login retry micro-waits capped at **200** ms per call — **LLD** **6.207**, **HLD** **1.128** |
| 3.127 | Apr 2026 | — | **FR-18b**: **`Playwright_insurance.txt`** — tab resolution **branch** + elapsed after **2W** / **New Policy**; KYC phase **`elapsed_ms`** slices; optional **`HERO_MISP_KYC_TAB_AWAY_SIMULATION`**; Sign In operator **`NOTE`** when already past partner login; VIN attach **attempt** count — **LLD** **6.208**, **HLD** **1.129** |
| 3.128 | Apr 2026 | — | **FR-18b**: Post–Sign In hub readiness before **2W** click (**`_hero_misp_after_sign_in_settle`**) — **LLD** **6.209**, **HLD** **1.130** |
| 3.129 | Apr 2026 | — | **FR-18b**: **main_process** — **`txtNomineeName`** commit/readback aligned with **`txtNomineeAge`**; **HDFC** payment via **`label[for=…rdoHdfcCCType]`** then radio — **LLD** **6.210**, **HLD** **1.131** |
| 3.130 | Apr 2026 | — | **FR-18b**: **`Playwright_insurance.txt`** — **`tab_resolve resolver_ms`**; KYC **`after_ovd_ready`** on eKYC keyboard path; **`HERO_MISP_KYC_TAB_AWAY_SIMULATION`** doc in **`.env.example`** — **LLD** **6.211**, **HLD** **1.132** |
| 3.131 | Apr 2026 | — | **FR-18b**: **main_process** — Hero CPI regex fix; **EME** add-on uncheck; **CC** payment mode before **HDFC** — **LLD** **6.212**, **HLD** **1.133** |
| 3.132 | Apr 2026 | — | **FR-18b**: **`Playwright_insurance.txt`** trace workflow doc (**`Documentation/playwright-insurance-trace-workflow.md`**); **pre_process** — MISP tab resolver staged waits + same-tab fast path; eKYC OVD ArrowDown settle default **48** ms — **LLD** **6.213**, **HLD** **1.134** |
| 3.133 | Apr 2026 | — | **FR-18b**: **pre_process** — VIN **`MispDms`** URL wait + **`txtFrameNo`** attach pacing restored to pre-**6.213** behavior (**≥3** s URL floor, **selector × frame**, **8** s per attach attempt) — **LLD** **6.214**, **HLD** **1.135** |
| 3.134 | Apr 2026 | — | **FR-18b**: **`main_process`** / **`insert_insurance_master_after_gi`** — no pre-commit **`insurance_master`** JSON **`NOTE`** in **`Playwright_insurance.txt`** — **LLD** **6.215**, **HLD** **1.136** |
| 3.135 | Apr 2026 | — | **FR-18b**: **main_process** add-on CPH1 checkboxes — stable DOM + PW readback before **`NOTE`** success (RTI/RSA) — **LLD** **6.216**, **HLD** **1.137** |
| 3.136 | Apr 2026 | — | **FR-23** Aadhaar Textract **heuristic name** when QR/keys are weak: scored Latin lines; letter layout uses lines between **Government of India** and **Aadhaar no. issued** so garbage tokens before the holder name do not win — **LLD** **6.217**, **§2.3** |
| 3.137 | Apr 2026 | — | **FR-23** / **FR-5** Sales Detail sheet: when **Profession** is blank but **Marital Status** is on the same row, OCR must not store marital text as **profession** — **`_sanitize_details_profession_value`** — **LLD** **6.218**, **§2.3** |
| 3.138 | Apr 2026 | — | **FR-5** / **FR-23**: **customer.name** — reconcile Aadhaar parse with Details **Full Name** (fuzzy **>0.5**); if not, pick best-matching name phrase from Aadhaar scan text vs Details — **`_reconcile_customer_name_aadhar_details`** — **LLD** **6.219**, **§2.3** |
| 3.139 | Apr 2026 | — | **FR-5**: Rule 2 — fuzzy scan of Aadhaar OCR uses Details **core** name (**S/o**/**D/o**/**W/o** stripped), not the full line — **LLD** **6.220**, **§2.3** |
| 3.140 | Apr 2026 | — | **FR-5** / **FR-23**: Profession must not take **Marital Status** bleed (incl. **`- Marital Status: Unmaried`** / glued **maritalstatus**); **`_apply_initcap_on_read`** sanitizes **customer**/**insurance** profession — **LLD** **6.221**, **§2.3** |
| 3.141 | Apr 2026 | — | **FR-5** / **FR-18b**: Marital status OCR typo **Unmaried** → stored/display **Single**; MISP proposal mapping aligned — **LLD** **6.222**, **§2.3** |
| 3.142 | Apr 2026 | — | **FR-23** / **FR-18b**: Blank **Insurer Name** must not take the next printed consent/SMS line; **`sanitize_details_sheet_insurer_value`**; when insurer still empty, **`build_insurance_fill_values`** uses **`dealer_ref.prefer_insurer`** — **LLD** **6.223**, **§2.3** / **§6.6** |
| 3.143 | Apr 2026 | — | **FR-23**: Nominee **Relation** must not retain a trailing period from the form (e.g. **Mother.** → **Mother**) — **`normalize_nominee_relationship_value`** — **LLD** **6.224**, **§2.3** |
| 3.144 | Apr 2026 | — | **FR-5** / **FR-23**: Profession must not remain **Marital Status: Unmaried** (incl. OCR **Martial**, full-width colon, whole-line bleed) — **`_sanitize_details_profession_value`**, **`submit_info_service`**, **`build_insurance_fill_values`** — **LLD** **6.225**, **§2.3** |
| 3.145 | Apr 2026 | — | **FR-5** / **FR-18b**: Blank profession after sanitization defaults to **Employed** (**`default_profession_if_empty`**) — **LLD** **6.226**, **§2.3** / **§6.6** |
| 3.146 | Apr 2026 | — | **FR-23** / **FR-18b**: Insurer must not be the printed SMS/consent line (title case, punctuation); sanitize on Submit/GI commit + Add Sales OCR merge — **LLD** **6.227**, **§2.3** / **§6.6** |
| 3.147 | Apr 2026 | — | **FR-16** / dealer **Pre-check**: Existing-row detection must not count unrelated tables — **`_siebel_run_vehicle_serial_detail_precheck_pdi`** jqGrid + Precheck scope — **LLD** **6.228**, **§2.4d** / **§6.1a** |
| 3.148 | Apr 2026 | — | **§6.1a** / operator log: **`Playwright_DMS*.txt`** no longer includes **`[frame-focus]`** lines — **`_siebel_note_frame_focus_snapshot`** no-op — **LLD** **6.229** |
| 3.149 | Apr 2026 | — | **§6.1a** / **PDI**: New-row **+** is **`Service Request List:New`** (not mandatory **`s_2_2_32_0_icon`**) — **LLD** **6.230** |
| 3.150 | Apr 2026 | — | **§6.1a** / **Contacts → Payments**: automation prefers **Payment Lines Save**, then **Ctrl+S**; verifies **Transaction#** with polling; operator **`Playwright_DMS*.txt`** errors distinguish save vs grid verification — **LLD** **6.255** |
| 3.151 | Apr 2026 | — | **§6.1a** / **Payments** tab: Third Level View Bar activation tries the shell document where **Payments** appears in the combo before nested **select** duplicates — **LLD** **6.257** |
| 3.152 | Apr 2026 | — | **§6.1a** / **Contacts → Payments**: tab activation avoids long Playwright stalls (JS match first; capped **`select_option`**) — **LLD** **6.258** |
| 3.153 | Apr 2026 | — | **§6.1a** / **Payments**: prefer Third Level shell found from **S_A1** / **parent_frame** (same lineage as Address postal) — **LLD** **6.259** |
| 3.154 | Apr 2026 | — | **§6.1a** / **Payments** tab: **`#s_vctrl_div.siebui-subview-navs`** strip + anchor/JS fallbacks — **LLD** **6.260** |
| 3.155 | Apr 2026 | — | **§6.1a** / operator diagnostics: after Address Line 1 fill, **Playwright_DMS** log may include per-frame element sample + **payHits** — **LLD** **6.261** |
| 3.156 | Apr 2026 | — | **BR-21** / **§6.1a** step 6 / **§6.7** / **§9**: Siebel **Run Report** batch (**GST Retail Invoice**, **GST Booking Receipt**) after staging commit; **`hero_dms_form22_print`**; PDF naming **`{mobile}_{Report_Name}.pdf`** — **LLD** **6.276**, **HLD** **1.157**, **Database DDL** **2.66** |
| 3.157 | Apr 2026 | — | **§6.1a** / **FR-18**: Fill DMS orchestration — **`fill_hero_dms_service.Playwright_Hero_DMS_fill`** chains **`prepare_vehicle`** → **`prepare_customer`** (**`hero_dms_prepare_customer`**) → **`prepare_order`** (**`hero_dms_playwright_invoice`**) → **`hero_dms_db_service.persist_masters_after_create_order`** (video INSERT path) / **`persist_staging_masters_after_invoice`** (staging); **`hero_dms_reports_service.run_hero_dms_reports`** wraps **`print_hero_dms_forms`** — **LLD** **6.277**, **HLD** **1.158** |
| 3.158 | Apr 2026 | — | **§6.1a** step **4b** / **§6.1c** **`vehicle_ex_showroom_price`**: optional **multi-line** booking attach — **`order_line_vehicles`** / **`attach_vehicles`** in DMS fill payload; **`_attach_vehicle_to_bkg`** loops **New** → **VIN** → **Discount** per line, then **Price All** / **Allocate**; **`order_line_ex_showroom`** scrape — **LLD** **6.278**, **HLD** **1.159** |
| 3.159 | Apr 2026 | — | **Implementation docs:** Add Sales / queue OCR modules renamed — **`ocr_service.py`** → **`sales_ocr_service.py`** (**`OcrService`**); **`textract_service.py`** → **`sales_textract_service.py`** (AWS Textract) — **LLD** **6.279**, **HLD** **1.160** |
| 3.160 | Apr 2026 | — | **BR-22** / **FR-25** / **§6.9**: Subdealer Challan — **`challan_staging`** workflow, **`prepare_vehicle`** loop + batch **`prepare_order`**, **`vehicle_inventory_master`** + **`subdealer_discount_master`**, **`challan_master`** / **`challan_details`** — **LLD** **§2.4e**, **6.280**, **HLD** **1.161**, **Database DDL** **2.70** |
| 3.161 | Apr 2026 | — | **BR-22** / **FR-25** / **§6.9**: Subdealer Challan staging split — **`challan_master_staging`** + **`challan_details_staging`**; Processed tab / **`staging/recent`**, **`retry-order`**, **`staging/failed-count`** — **LLD** **§2.4e**, **6.281**, **HLD** **1.162**, **Database DDL** **2.72** |
| 3.162 | Apr 2026 | — | **§6.3** / **FR-21a**–**FR-21c**: Vahan **workbench** RTO fill — **`fill_rto_service`** + **`form_vahan_view`** / **`insurance_master`**; stable **`workbench_tabview`** / **`hpa_*`** wiring; RTO log dumps only on **final** field or terminal failure — **LLD** **§2.4f**, **6.292**, **HLD** **1.165** |
| 3.163 | Apr 2026 | — | **FR-26** / **BR-23** / **BR-24**: Bulk pre-OCR documented in **HLD §4.3** (sale **`raw/`**, per-page **`page_NN.pdf`**, OSD upright, **`process_bulk_pdf`** skip duplicate copy) and **LLD §2.3a** / **§4.4** — **HLD** **1.166**; **LLD** **6.293** |
| 3.164 | Apr 2026 | — | **BR-23** / **FR-26**: **`raw/`** is **PDF-only** (`consolidated` + **`page_NN.pdf`**); no **`page_NN.jpg`**; OSD applied to single-page PDFs via **page rotation**; in-memory raster only for Tesseract — **HLD** **1.167**; **LLD** **6.294** |
| 3.165 | Apr 2026 | — | **BR-22** / **§6.9**: Subdealer challan per-line **discount** — **`dealer_ref.subdealer_type`** ( **`to_dealer_id`**) + **`subdealer_discount_master_ref`** ( **`from_dealer_id`**, type, model, **Y**); else **1500.00** — **LLD** **§2.4e**, **6.296**; **HLD** **1.168**; **Database DDL** **2.89** |
| 3.166 | Apr 2026 | — | **BR-22** / **§6.9**: **model** = DMS **prefix** match ( **`subdealer_discount_master_ref`**, longest) — **LLD** **§2.4e**, **6.297**; **HLD** **1.169**; **Database DDL** **2.90** |
| 3.167 | Apr 2026 | — | **FR-25** / **§6.9**: **Multi-page challan** — client **upload scan(s)** (multiple PDFs/images), **`parse-scan` per file**, **merge** book # (max) + lines; **one** **staging** + **process** batch; **`parseSubdealerChallanScans`**, **`mergeSubdealerChallanParseResults`**, **`maxChallanBookNumber`** — **HLD** **1.170**; **LLD** **6.298**, **§2.4e**; client **`SubdealerChallanPage`**, **`api/subdealerChallan.ts`** |
| 3.168 | Apr 2026 | — | **FR-18b** / **§6.4** / **§6.5**: **Generate Insurance** — single **`insurance_master` UPDATE** from post–**Issue Policy** scrape; no DB update from proposal preview alone; removed best-effort **Print Proposal**; **MISP Print Policy** ( **`AllPrintPolicy.aspx`**, two **Print** steps) + **`{mobile}_Insurance_{ddmmyyyy}.pdf`** (IST) + **`hero_insure_reports`** — **LLD** **6.299**–**6.300**, **HLD** **1.171** |
