"""
Built-in defaults for cross-cutting OCR and browser automation flags.

Not DMS- or Hero-specific. ``app.config`` uses these when the matching
environment variable is unset (env may still override).
"""

# Upload scans: run independent Textract API calls in parallel (Aadhar, Details, Insurance, …).
OCR_UPLOAD_PARALLEL_TEXTRACT: bool = True

# DMS Playwright: run the browser headed so the operator sees the page.
DMS_PLAYWRIGHT_HEADED: bool = True

# Legacy flag (unused). ``fill_dms_service`` does not keep Playwright open for operator sessions.
PLAYWRIGHT_KEEP_OPEN: bool = False
