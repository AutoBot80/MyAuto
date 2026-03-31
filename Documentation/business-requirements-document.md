# Business Requirements Document (BRD)
## Auto Dealer Management System — Arya Agencies

**Version:** 2.9  
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
| BR-19 | Siebel Contact Find and Add Enquiry persistence | On **real** Hero Connect / Siebel (`DMS_MODE=real`), Contact **Find** uses **Mobile** + **Contact First Name**; first name must be present and must not be a placeholder (**§6.1b**). The **First Name** query field is typed **exactly** (no wildcard). **Grid / automation row detection** after Find uses the **legacy** Hero-aligned rules (**§6.1b**): first-token and prefix matching on row text and cells, optional **mobile-only** acceptance when the name is not in the DOM, plus duplicate-row handling — not strict cell-only exact equality. **Open enquiry** (incl. **Enquiry Status** = Open when HHML fields exist), the **Enquiry# post-save gate** (timed polls) on **Add Enquiry**, and **video SOP orchestration** (**LLD 6.67**): **N=0** mobile drilldown rows → **Add Enquiry**; else title sweep for **Open**; if **no Open**, **Address** + **Postal Code** + Save on branch (2); **dotted suffixed first name** is **not** used on the **video** path. Further normative detail in **§6.1b**. |
| BR-20 | Generate Insurance inputs | **Generate Insurance** runs only after **Create Invoice** has persisted **`sales_master`**, **`customer_master`**, and **`vehicle_master`** (commit wave after successful DMS), so **`form_insurance_view`** returns the sale-linked projection. **`add_sales_staging.payload_json`** holds the merged OCR / operator snapshot from Submit. **Together** — view + staging — are the **complete approved input set**. The Add Sales client passes **`customer_id`** and **`vehicle_id`** from the **Create Invoice** response (or legacy flow) and the same **`staging_id`** (**`insurance_form_values.build_insurance_fill_values`**). **`OCR_To_be_Used.json`** is used **only** as a last-resort **insurer** fallback when both view and staging lack insurer. **No** **`insurance_master`** write on Submit; on **successful** Generate Insurance, the backend **INSERT**s **`insurance_master`** for the current calendar **`insurance_year`** (**fails** if **`(customer_id, vehicle_id, insurance_year)`** already exists). Nominee/insurer from fill dict; **policy number** and **`insurance_cost`** from the **policy preview** before **Issue Policy** when scraped; other policy fields from staging when present. Playwright then clicks **Issue Policy** and scrapes **policy number** and **`insurance_cost`** again; **`update_insurance_master_policy_after_issue`** updates those columns on the same row (operators are not expected to pay/issue twice for the same sale/year). |

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
- **FR-18** Fill DMS: Playwright reads DMS fill values from **OCR merge in `add_sales_staging`** (target) or, until staging is wired end-to-end, the **same projection as the former `form_dms_view`** loaded via **`form_dms.py`** (inline `sales_master` + `customer_master` + `vehicle_master` join after Submit Info). **`form_dms_view` is not used.** **Target real-DMS step order** is **§6.1a**; **normative Contact Find, grid match, open enquiry, suffixed first name, and Enquiry# save gate:** **§6.1b** (and **BR-19**); implementation parity: **LLD §2.4d** / **LLD** changelog **6.8**. Static training DMS HTML and **`DMS_MODE=dummy`** were removed; **`DMS_MODE`** defaults to **real** Siebel. **`"DMS Contact Path"`** / **`skip_find`** in persisted data **does not** skip Contact Find on real Siebel — automation **always** runs Find first (`DMS_REAL_URL_CONTACT`, **mobile + Contact First Name** + Go; empty or placeholder first name fails — **§6.1b**); **`skip_find` is not a bypass**. **Generate Booking** runs **after** vehicle processing; allotment when **not** In Transit (see **LLD §2.4d**). **After find:** existing match → care-of only + Save; no match or **`new_enquiry`** → basic enquiry + Save + mandatory re-find + care-of; vehicle scrape with **In Transit** detection; branch receipt → **Pre Check** → **PDI** vs booking/allotment per **§6.1a**. Implementation: **`siebel_dms_playwright.run_hero_siebel_dms_flow`**; **`DMS_Form_Values.txt`** / **`Playwright_DMS.txt`**; tune **`DMS_SIEBEL_*`** and **`DMS_REAL_URL_*`**. **Merge rule:** Fill DMS persists scraped **full** chassis/engine and related fields into `vehicle_master` but **does not overwrite** `raw_frame_num` / `raw_engine_num` (those stay Submit Info / Sales Detail Sheet so partial VIN/engine for Siebel vehicle search match operator entry).
- **FR-18a** Existing tab reuse with operator fallback: DMS/Vahan steps first reuse already open logged-in tabs; if none are detectable, API opens Edge/Chrome to the target site and returns a user-facing message asking the operator to login (first-time) and retry.
- **FR-18b** Insurance fill step: Playwright fills Insurance portal fields from **`form_insurance_view`** (after sale rows exist) merged with **`add_sales_staging.payload_json`** for the same **`staging_id`** (**BR-20**). Add Sales always supplies **`staging_id`** with **Generate Insurance** so the OCR snapshot and committed masters are used together. **`OCR_To_be_Used.json`** is only an insurer fallback when view and staging both lack insurer. Reuses an already open logged-in insurance tab (or opens browser and asks operator to login first-time) and keeps the browser open for operator review. After **Proposal Review**, the flow scrapes the **policy preview** (before **Issue Policy**) for **`policy_num`** and **`insurance_cost`**, then **INSERT**s **`insurance_master`** for the current calendar year via **`add_sales_commit_service.insert_insurance_master_after_gi`** (**fails** if **`(customer_id, vehicle_id, insurance_year)`** already exists — **`uq_insurance_customer_vehicle_year`**). Playwright clicks **Issue Policy**; **`click_issue_policy_and_scrape_preview`** scrapes **`policy_num`** and **`insurance_cost`** again; **`add_sales_commit_service.update_insurance_master_policy_after_issue`** updates those columns on the row for the current year.
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
| 4b | **Branch: not In Transit (booking path)** | **Enquiry → My Enquiries** (or current enquiry): **Generate Booking** when creating/linking the sales order. **Vehicle Sales → Invoice** (order): **Allotment** tab — **Price All** / **Allocate** (and related line actions) as required. |
| 5 | **Tenant-dependent gates (if shown)** | Handle or stop for operator: **Vehicle Digitization** (e.g. OTP), **Document Upload**, **Sanction Details**, **Validate GL Voucher**, **WOT Details** (if exchange), **Contacts → Payments**, finance/hypothecation dialogs. No invented clicks — follow persisted flags and visible prompts. |
| 6 | **End of automation** | **Do not** click **Create Invoice**. Leave the **browser window open** for operator review (same session discipline as insurance automation). **Run Report** / GST PDF downloads are out of scope unless added under a separate FR. |

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
- **`vehicle_ex_showroom_price`**: scraped after **Price All** / **Allocate All** in the booking-attach path (**`_attach_vehicle_to_bkg`**).
- **Uniqueness:** keep **`uq_vehicle_raw_triple`** on raw columns and enforce a **partial unique index** on populated **`chassis`** (canonical VIN). Column **`dms_sku`** is **dropped**.

### 6.1d `sales_master` (reference)

- **`order_number`**, **`invoice_number`**, and **`enquiry_number`** are **scraped from Siebel at different points** during the DMS run (enquiry / order / invoice stages — not a single screen). Persistence uses **`update_sales_master_from_dms_scrape`** as each value becomes available (`COALESCE` merge on the sale row).
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

### 6.4 Insurance Fields to Fill (Submit Info Contract)

- Insurance and related customer fields from Submit Info are validated and stored in **`add_sales_staging.payload_json`** (`nominee_gender` under **insurance** until **Generate Insurance** commits). **`insurance_master`** is not written on Submit; after **successful Generate Insurance**, the API **INSERT**s **`insurance_master`** for the current **`insurance_year`** (**fails** on duplicate **`(customer_id, vehicle_id, insurance_year)`**), including **`nominee_gender`**; nominee/insurer from the MISP fill dict; initial policy # and **`insurance_cost`** from preview scrape when present; dates, premium, idv, broker from staging when present. After **Issue Policy**, the same row’s **`policy_num`** / **`insurance_cost`** are refreshed from a second scrape (**FR-18b**). On master commit after **Create Invoice**, **`customer_master`** receives profession/financier/marital/care_of/DMS path fields from the staging snapshot (**FR-17**); per-sale **`file_location`** is **`sales_master.file_location`** (mirrored on **`customer_master.file_location`** on commit).
- Hero Insurance: after **Create Invoice**, **`form_insurance_view`** plus **`add_sales_staging.payload_json`** (same **`staging_id`** as DMS) supply the automation inputs — committed sale/vehicle/customer context from the view and the full OCR/operator merge from staging (**BR-20**). **Email, add-ons, CPA tenure, payment mode, and registration date** on the proposal page may use **hardcoded** defaults in Playwright until optional columns exist. Insurer may fall back to **`OCR_To_be_Used.json`** only when view and staging lack it.

### 6.5 Insurance Navigation Sequence (Video-Aligned)

1. Login page (`misp.heroinsurance.com` / dummy `index.html`): operator enters credentials and **Login**; Playwright waits (up to `INSURANCE_LOGIN_WAIT_MS`) until KYC is shown, then automates KYC.
2. KYC verification page (`ekycpage.aspx` / dummy `kyc.html`): enter mobile → **Verify mobile**; if KYC not on file, upload three documents (Aadhaar front, rear, customer photo) → consent → **Submit** to advance; dummy uses `#ins-check-mobile` / `#ins-kyc-submit` / `policy.html` flow.
3. KYC success auto-redirect screen
4. MisDMS policy entry page (`MispDms.aspx`) with VIN input
5. New policy creation page (`MispPolicy.aspx`) for "New Policy - Two Wheeler"
6. (Optional reference tab) Hero Connect lookup for invoice/vehicle context, then return to MisDMS policy flow
7. **Proposal Review** → preview scrape → **`insurance_master` INSERT** (current calendar year; **fails** if that triple already exists) → **Issue Policy** → scrape **`policy_num`** / **`insurance_cost`** again → **UPDATE** the same row (**FR-18b**)

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
| New Policy | Ex-Showroom | Vehicle price | `vehicle_master.vehicle_ex_showroom_price` (via `form_vahan_view.vehicle_price`) |
| New Policy | RTO | Dealer RTO mapping | `dealer_ref.rto_name` |
| New Policy | Nominee Name/Age/Relation | Insurance nominee details | `insurance_master.nominee_name`, `insurance_master.nominee_age`, `insurance_master.nominee_relationship` |
| New Policy | Nominee Gender | Staging until policy commit | `insurance_master.nominee_gender` (`form_insurance_view`) |
| New Policy | Financer Name | Finance context from details sheet | `customer_master.financier` |
| New Policy | Email / add-ons / CPA / payment / reg. date (proposal) | Hardcoded Playwright defaults | Not persisted (optional future columns) |

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
- Submit Info persists a **draft** **`add_sales_staging`** row only (**`POST /submit-info`**, **`staging_id`**); **Create Invoice** uses **`payload_json`** via **`staging_id`** then commits masters; **Generate Insurance** uses committed IDs + **`staging_id`** (**FR-17**, **LLD §2.2a**).
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
