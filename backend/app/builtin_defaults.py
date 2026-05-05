"""
Built-in defaults for cross-cutting OCR and browser automation flags.

Not DMS- or Hero-specific. ``app.config`` uses these when the matching
environment variable is unset (env may still override).
"""

# Upload scans: run independent Textract API calls in parallel (Aadhar, Details, Insurance, …).
OCR_UPLOAD_PARALLEL_TEXTRACT: bool = True

# DMS Playwright: run the browser headed so the operator sees the page.
DMS_PLAYWRIGHT_HEADED: bool = True

# DMS + Insurance (MISP) + Vahan: Playwright-bundled Chromium with launch_persistent_context (no CDP Edge).
USE_NATIVE_PLAYWRIGHT_CHROMIUM_FOR_DMS: bool = True

# DMS Siebel login: max ms to wait for the operator to finish manual login before failing Create Invoice.
DMS_LOGIN_MANUAL_WAIT_MS: int = 120_000

# Poll Siebel login for Chrome autofill / operator pause before programmatic Login (see ``handle_browser_opening``).
DMS_BROWSER_AUTOFILL_LOGIN_MAX_MS: int = 180_000

# Legacy flag (unused). ``fill_dms_service`` does not keep Playwright open for operator sessions.
PLAYWRIGHT_KEEP_OPEN: bool = False
