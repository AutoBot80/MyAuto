# Dummy sites for Playwright automation

Local mock sites used to develop and test Playwright automation (form fill, navigation, file download) without hitting real DMS or Parivahan/Vaahan.

## DMS (Dealer Management System)

Layout and field names mirror the Hero MotoCorp / Oracle Siebel Enquiry screen (eDealer HMCL).

- **Base URL** (when backend is running): `http://localhost:8000/dummy-dms/`
- **Login**: `index.html` — any username/password accepted (redirects to Enquiry).
- **Enquiry**: `enquiry.html` — Customer Information, Address, Customer Profile, Vehicle Information, Finance Details, Enquiry Information. All inputs have stable `id` and `name` attributes for Playwright selectors (e.g. `#dms-contact-first-name`, `#dms-mobile-phone`, `#dms-state`, `#dms-pin-code`).
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
