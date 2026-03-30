-- Total / payable premium: scraped from policy preview before Issue Policy, then refreshed after Issue Policy (same Generate Insurance run).
-- Run after insurance_master exists.

ALTER TABLE insurance_master
    ADD COLUMN IF NOT EXISTS insurance_cost NUMERIC(12, 2);

COMMENT ON COLUMN insurance_master.insurance_cost IS 'Total premium / insurance cost: preview before Issue Policy, updated after Issue Policy scrape (MISP)';
