-- Drop phone column; identify customer by (aadhar, mobile_number).
-- Run against database: auto_ai

ALTER TABLE customer_master DROP CONSTRAINT IF EXISTS uq_customer_aadhar_phone;
ALTER TABLE customer_master DROP COLUMN IF EXISTS phone;
ALTER TABLE customer_master ADD CONSTRAINT uq_customer_aadhar_mobile UNIQUE (aadhar, mobile_number);

COMMENT ON COLUMN customer_master.mobile_number IS 'Customer mobile (10 digits); (aadhar, mobile_number) uniquely identify customer';
