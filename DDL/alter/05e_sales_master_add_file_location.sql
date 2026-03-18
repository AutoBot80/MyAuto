-- Add per-sale document folder so View Customer can open the correct files per vehicle.
-- Run after: 05_sales_master.sql

ALTER TABLE sales_master
ADD COLUMN IF NOT EXISTS file_location VARCHAR(128);
