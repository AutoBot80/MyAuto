# Dummy sites for Playwright automation

Local mock sites used to develop and test Playwright automation (form fill, navigation, file download) without hitting real DMS or Parivahan/Vaahan.

**Configuration:** The API server requires absolute URLs in `backend/.env` (`DMS_BASE_URL`, `VAHAN_BASE_URL`, `INSURANCE_BASE_URL`) pointing at these dummy mounts — see `backend/.env.example`. There are no in-code fallbacks; the app will not start if any are missing.

## DMS (Dealer Management System)

Shell matches **Hero Connect / Oracle Siebel eDealer** (`edealerHMCL`) from the **DMS Process Video**: shared chrome (`dms-layout.css`), **Find** saved-query strip, and top tabs — **Home**, **My Dashboard**, **Payments**, **Vehicles**, **Enquiry**, **Vehicle Sales Invoices**, **Vehicles Receipt**, **Vehicles Inventory**, **Vehicle Sales**.

- **Base URL** (when backend is running): `http://localhost:8000/dummy-dms/`
- **Styles**: `dms-layout.css` — Siebel-grey header, green active tab, sub-tabs, inner tabs row.
- **Login**: `index.html` — **User ID**, **Password**, **Remember my User ID**; submits to **`enquiry.html`** (dummy skips real SSO).
- **Enquiry**: `enquiry.html` — **Contact finder** (`#dms-contact-finder-go`, `sessionStorage dummy_dms_expect=new` for “not found”); **Register new enquiry**; **Relation (S/O or W/o)**, **Father / Husband name**; **Save enquiry (Playwright)** (`#dms-save-enquiry-quiet`); **Generate Booking** (sets `data-dms-booking-done`, validates **Booking Order Type\***); **Allocate vehicle** (`#dms-allocate-vehicle`); **Financier Name** as text field; same core IDs (`#dms-contact-first-name`, …).
- **Vehicle Sales**: `my-sales.html` — sub-tabs **Sales Orders Home | Invoice | Rural Vertical Mismatch | Delivery Challan**; toolbar **Create Invoice**, **Cancel Booking**, **Validate Offer**, **WOT Details**, **Verify OTP**, **Validate GL Voucher**; **My Vehicle Sales** list.
- **Invoice / order line**: `line-items.html` — inner tabs **Allotment | Invoices | New Payments | Contact Email | Booking Follow-Up | Corporate Identity | Document Upload | Sanction Details | More Info**; order header **Order:**, **Finance Required**, **Financer**, **Hypothecation**, **Order Value**; **Allotment** line + link to vehicle search.
- **Auto Vehicle List (Allotment)**: `vehicle.html` — same **Key / Frame / Engine** search and `#dms-vehicle-results-table` as before (Playwright scrape contract unchanged).
- **Vehicles (vehicle record)**: `vehicles.html` — **eAuto Vehicle Features View**; **Insurance** (Company, Policy #, Policy Expiry Date, BAAS), connectivity / service fields; inner tab labels **Contacts | PDI | Features and Image | …**.
- **Contacts / Payments**: `contacts-payments.html` — **Contact Site Payments View**; **Payment Lines**, **Transaction Amount**, **Account\***, **Payment Details – Cash**.
- **PDI**: `pdi.html` — **Complete PDI** `#dms-pdi-complete` (same inner tab strip as Vehicles).
- **Reports / Run Report**: `reports.html` — **Report Name**, **Output Type** (PDF), **Submit**; downloads **Form 21 / 22 / Invoice Details** (`#dms-reports-form21`, `#dms-reports-form22`, `#dms-reports-invoice-details`).
- **View / Print invoice**: `invoice.html` — print / dummy invoice PDF + **GST Invoice sheet** link (`downloads/invoice_details.pdf`).

### Run

1. Start the backend from the project root (e.g. `cd backend && uvicorn app.main:app --reload`).
2. Open: [http://localhost:8000/dummy-dms/](http://localhost:8000/dummy-dms/)
3. Use the same base URL in Playwright (e.g. `page.goto(baseUrl + '/dummy-dms/')`).

## Vaahan (Parivahan-style)

Dummy vehicle registration flow: login → application form (all sections) → payment.

- **Base URL** (when backend is running): `http://localhost:8000/dummy-vaahan/`
- **Login**: `index.html` — any credentials (redirects to application).
- **Application**: `application.html` — single long form with 8 sections:
  1. **Vehicle Details** – vehicle class, maker, model, variant, fuel type, colour, chassis number, engine number, month/year of manufacture, seating capacity, cubic capacity, unladen weight.
  2. **Owner Details** – owner name, father/husband name, owner type, DOB, gender, mobile, email.
  3. **Address Details** – house/flat, street/locality, village/town, district, state, pincode.
  4. **Dealer / Invoice Details** – dealer code, invoice number, invoice date, ex-showroom price, GST, sale type.
  5. **Insurance Details** – company, policy number, start/expiry dates, policy type.
  6. **Finance / Hypothecation** – hypothecation (Y/N), financier name, branch, loan agreement number.
  7. **Tax Calculation** – tax mode, tax amount, registration fee, smart card fee.
  8. **Documents Uploaded** – list of mandatory docs (Form 20, Form 21, Form 22, insurance, ID proof, address proof); no file upload in dummy.
- **Payment**: link **Proceed to Payment** goes to `payment.html` — dummy total and **Pay Now (dummy)** button; on click shows “Payment successful (dummy)”.

All form fields use stable `id` attributes prefixed with `vahan-` (e.g. `#vahan-vehicle-class`, `#vahan-owner-name`, `#vahan-payment-link`) for Playwright.

## Insurance (MISP/HIBIPL-style)

Dummy insurance policy issuance flow aligned to operator video:
KYC -> KYC success redirect -> MisDMS VIN entry -> New Policy - Two Wheeler.

- **Base URL** (when backend is running): `http://localhost:8000/dummy-insurance/`
- **Login/Redirect**: `index.html` -> `kyc.html`
- **KYC**: `kyc.html` — enter **Mobile No.** and click **Verify mobile**. If **KYC found** (demo: `9694585832`), proceed with consent only. If **KYC not found**, upload **Aadhaar front**, **Aadhaar rear**, and for **Customer Photo** use the **Aadhaar front** file again, then consent and **Proceed**. Playwright mirrors this (tiny PNG placeholders when uploads are required).
- **KYC Success**: `kyc-success.html` — auto-redirects to MisDMS in 2 seconds.
- **MisDMS Entry**: `dms-entry.html` — VIN / Frame field is the **chassis number** (same as from DMS).
- **New Policy**: `policy.html` — **Ex-Showroom (DMS cost)** maps to vehicle price scraped from DMS (`vehicle_master.vehicle_ex_showroom_price`). **Insurance company** and **manufacturer** dropdowns include multiple labels; Playwright **fuzzy-matches** DB insurer (details sheet) and OEM name (`vehicle_master.oem_name` / dealer `oem_ref`). **Policy tenure** and **proposer type** stay as page defaults. **Issue Policy** (`#ins-issue-policy`) is operator-only; Playwright does not click it.
- **Issue Result (dummy)**: `issued.html` — simulated policy-issued page.

Notes:
- This site mirrors labels and flow order from the video; no real policy APIs are called.
- Hero Connect lookup is represented as an external link from `dms-entry.html` to the existing dummy DMS flow.
