-- Add "File location" column to customer_master (location/sub-folder where scans are placed).
-- Run against database: auto_ai

ALTER TABLE customer_master
ADD COLUMN IF NOT EXISTS file_location TEXT;

COMMENT ON COLUMN customer_master.file_location IS 'Location/sub-folder name where scans are placed';
