-- Drop deprecated RTO scrape-back fields from vehicle_master.
-- These values now live on sales_master per sale.

ALTER TABLE vehicle_master
DROP COLUMN IF EXISTS vahan_application_id;

ALTER TABLE vehicle_master
DROP COLUMN IF EXISTS rto_charges;
