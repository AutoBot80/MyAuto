-- Add profession to customer_master.
-- Run against database: auto_ai

ALTER TABLE customer_master
ADD COLUMN IF NOT EXISTS profession VARCHAR(16);

COMMENT ON COLUMN customer_master.profession IS 'Customer profession (e.g. Service, Business), up to 16 chars';

