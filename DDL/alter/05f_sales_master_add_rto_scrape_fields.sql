-- Add RTO scrape-back fields to sales_master so the latest Vahan application id
-- and RTO charges are stored per sale instead of per vehicle.

ALTER TABLE sales_master
ADD COLUMN IF NOT EXISTS vahan_application_id VARCHAR(128);

ALTER TABLE sales_master
ADD COLUMN IF NOT EXISTS rto_charges NUMERIC(12, 2);

COMMENT ON COLUMN sales_master.vahan_application_id IS 'Latest Vahan application id scraped during RTO queue processing';
COMMENT ON COLUMN sales_master.rto_charges IS 'Latest Vahan RTO charges scraped during RTO queue processing';
