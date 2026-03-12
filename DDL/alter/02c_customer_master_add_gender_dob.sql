-- Add gender and date_of_birth to customer_master (for QR / Aadhar granular data).
-- Address remains a single column, constructed from care of, house, street, location when saving from QR.
-- Aadhar column stays as last 4 digits only (legal compliance); full number shown only on frontend.
-- Run against database: auto_ai

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS gender VARCHAR(8),
  ADD COLUMN IF NOT EXISTS date_of_birth VARCHAR(20);

COMMENT ON COLUMN customer_master.gender IS 'Gender from Aadhar QR (e.g. M, F)';
COMMENT ON COLUMN customer_master.date_of_birth IS 'Date of birth, dd/mm/yyyy (default date format for application and database)';
