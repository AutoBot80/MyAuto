# Dummy sites for Playwright automation

Local mock sites used to develop and test Playwright automation (form fill, navigation, file download) without hitting real DMS or Parivahan/Vaahan.

**Configuration:** The API server requires absolute URLs in `backend/.env` (`DMS_BASE_URL`, `VAHAN_BASE_URL`, `INSURANCE_BASE_URL`) pointing at these dummy mounts — see `backend/.env.example`. There are no in-code fallbacks; the app will not start if any are missing.

## DMS (Dealer Management System)

Layout and labels now mirror the operator video flow: Hero Connect login -> Oracle Siebel Vehicle Sales/Line Items -> Auto Vehicle List/PDI -> MISP/Partner Login.

- **Base URL** (when backend is running): `http://localhost:8000/dummy-dms/`
- **Login**: `index.html` — Hero Connect style login (`User ID`, `Password`, `Remember my User ID`); any credentials accepted.
- **My Vehicle Sales**: `my-sales.html` — sales list and action labels (`Create Invoice`, `Send Email`, `ReSend SMS`, `Apply Campaign`).
- **Line Items**: `line-items.html` — invoice/detail view and transition to Vehicle.
- **Vehicle List**: `vehicle.html` — `Auto Vehicle List` with existing search/result selectors used by Playwright.
- **PDI**: `pdi.html` — `Auto Vehicle PDI Assessment` labels/tabs and submit transition.
- **MISP / Partner Login**: `reports.html` — MISP dashboard labels and partner-login context, with report downloads.
- **View / Print invoice**: link "View / Print invoice" navigates to `invoice.html`. On that page you can use **Print** (browser print dialog) or **Download PDF** for the basic invoice PDF.

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
- **KYC**: `kyc.html` — insurer, KYC partner, proposer type, OVD type, mobile, Aadhaar front/rear, customer photo, consent, and proceed.
- **KYC Success**: `kyc-success.html` — auto-redirects to MisDMS in 2 seconds.
- **MisDMS Entry**: `dms-entry.html` — left-menu labels + VIN entry and proceed.
- **New Policy**: `policy.html` — proposer, address, vehicle, nominee, financer, add-on, payment sections.
- **Issue Result (dummy)**: `issued.html` — simulated policy-issued page.

Notes:
- This site mirrors labels and flow order from the video; no real policy APIs are called.
- Hero Connect lookup is represented as an external link from `dms-entry.html` to the existing dummy DMS flow.
