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

To add a second dummy site for Vaahan/Parivahan, create `dummy-sites/vaahan/` with `index.html` (and any forms/search/download pages), then mount it in `backend/app/main.py` similarly:

```python
_DUMMY_VAHAN = _PROJECT_ROOT / "dummy-sites" / "vaahan"
if _DUMMY_VAHAN.is_dir():
    app.mount("/dummy-vaahan", StaticFiles(directory=str(_DUMMY_VAHAN), html=True), name="dummy-vaahan")
```

Then use `http://localhost:8000/dummy-vaahan/` in Playwright.
