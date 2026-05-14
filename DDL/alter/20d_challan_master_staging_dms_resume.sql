-- Subdealer challan: Siebel order# and attach progress for retry/resume (run after challan_master_staging exists).

ALTER TABLE challan_master_staging
    ADD COLUMN IF NOT EXISTS dms_order_number VARCHAR(128),
    ADD COLUMN IF NOT EXISTS dms_attached_vin_count INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN challan_master_staging.dms_order_number IS 'Siebel sales order# after booking Ctrl+S (non-TXN); used for My Orders Order# search on retry';
COMMENT ON COLUMN challan_master_staging.dms_attached_vin_count IS 'Lines with VIN+discount complete during last attach (0..num_vehicles); hint for UI and resume';
