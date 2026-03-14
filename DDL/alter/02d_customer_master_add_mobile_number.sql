-- Add mobile_number (10-digit) to customer_master.
-- Run against database: auto_ai

ALTER TABLE customer_master
ADD COLUMN IF NOT EXISTS mobile_number INTEGER;

COMMENT ON COLUMN customer_master.mobile_number IS 'Customer mobile number (10 digits)';
