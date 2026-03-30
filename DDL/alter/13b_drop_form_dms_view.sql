-- Remove form_dms_view: Create Invoice loads the same column set via an inline query in
-- backend/app/repositories/form_dms.py (and future: add_sales_staging.payload_json from OCR).
-- Safe to run if the view was never created.

DROP VIEW IF EXISTS form_dms_view;
