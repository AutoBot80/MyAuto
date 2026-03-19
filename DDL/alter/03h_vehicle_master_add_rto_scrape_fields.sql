-- Add Vahan scrape-back fields to vehicle_master so RTO batch retries can overwrite them.

ALTER TABLE vehicle_master
ADD COLUMN IF NOT EXISTS vahan_application_id VARCHAR(128);

ALTER TABLE vehicle_master
ADD COLUMN IF NOT EXISTS rto_charges NUMERIC(12, 2);

COMMENT ON COLUMN vehicle_master.vahan_application_id IS 'Latest Vahan application id scraped during RTO queue processing';
COMMENT ON COLUMN vehicle_master.rto_charges IS 'Latest Vahan RTO charges scraped during RTO queue processing';
