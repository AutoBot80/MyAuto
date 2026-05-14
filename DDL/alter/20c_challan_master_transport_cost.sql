-- Subdealer challan: optional transport cost per vehicle (deducted from line discount in order phase).
-- Run against DBs that already have challan_master_staging and challan_master.

ALTER TABLE challan_master_staging
    ADD COLUMN IF NOT EXISTS add_transport_cost BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS transport_cost_per_vehicle NUMERIC(12, 2);

COMMENT ON COLUMN challan_master_staging.add_transport_cost IS 'When true, transport_cost_per_vehicle is subtracted from each line discount before Siebel attach';
COMMENT ON COLUMN challan_master_staging.transport_cost_per_vehicle IS 'Per-vehicle transport amount (same currency as discount); used only when add_transport_cost is true';

ALTER TABLE challan_master
    ADD COLUMN IF NOT EXISTS add_transport_cost BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS transport_cost_per_vehicle NUMERIC(12, 2);

COMMENT ON COLUMN challan_master.add_transport_cost IS 'Snapshot from staging at commit';
COMMENT ON COLUMN challan_master.transport_cost_per_vehicle IS 'Snapshot from staging at commit; per-vehicle amount deducted from discount when flag was true';
