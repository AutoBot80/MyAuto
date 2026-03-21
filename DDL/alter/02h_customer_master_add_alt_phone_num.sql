-- Add alternate/landline phone field for DMS/Insurance form fills.

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS alt_phone_num VARCHAR(16);

COMMENT ON COLUMN customer_master.alt_phone_num IS 'Alternate/landline customer number from Sales Detail Sheet (Alternate)';
