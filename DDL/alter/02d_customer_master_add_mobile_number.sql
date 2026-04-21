-- Add mobile_number (10-digit) to customer_master.
-- Run against database: auto_ai

ALTER TABLE customer_master
ADD COLUMN IF NOT EXISTS mobile_number BIGINT;

COMMENT ON COLUMN customer_master.mobile_number IS
    'Customer mobile (10 digits; BIGINT — INTEGER too small for 6–9… range)';
