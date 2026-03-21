-- Care of from Aadhaar QR (XML "co" / Care of); used for DMS Father/Husband name and Form 20 name line.
-- Run against database: auto_ai (after customer_master exists).

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS care_of VARCHAR(255);

COMMENT ON COLUMN customer_master.care_of IS 'Care of / father or husband name from Aadhaar QR; same semantic as OCR care_of';
