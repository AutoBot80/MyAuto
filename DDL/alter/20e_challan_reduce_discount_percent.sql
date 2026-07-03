-- Subdealer challan: optional percentage reduction from line discount (with cost per vehicle).
-- Run against DBs that already have challan_master_staging and challan_master.

ALTER TABLE challan_master_staging
    ADD COLUMN IF NOT EXISTS reduce_discount_by_percent NUMERIC(5, 2);

COMMENT ON COLUMN challan_master_staging.reduce_discount_by_percent IS 'When add_transport_cost is true, percent of base discount subtracted per line before cost per vehicle (e.g. 10 = 10%)';

ALTER TABLE challan_master
    ADD COLUMN IF NOT EXISTS reduce_discount_by_percent NUMERIC(5, 2);

COMMENT ON COLUMN challan_master.reduce_discount_by_percent IS 'Snapshot from staging at commit; percent of base discount deducted when flag was true';
