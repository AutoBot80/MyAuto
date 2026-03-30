-- Hero Connect / Siebel Contact Id scraped from contact detail (e.g. 11870-01-SCON-…).
-- Run against database: auto_ai

ALTER TABLE customer_master
  ADD COLUMN IF NOT EXISTS dms_contact_id VARCHAR(128);

COMMENT ON COLUMN customer_master.dms_contact_id IS 'DMS / Siebel Contact Id from automation scrape (optional)';
