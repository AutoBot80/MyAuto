-- challan_staging: batch grouping, processing status, inventory link. Run after challan_staging exists.

ALTER TABLE challan_staging ADD COLUMN IF NOT EXISTS challan_batch_id UUID;
ALTER TABLE challan_staging ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE challan_staging ADD COLUMN IF NOT EXISTS inventory_line_id INTEGER;

COMMENT ON COLUMN challan_staging.challan_batch_id IS 'Groups rows created in one Create Challans action';
COMMENT ON COLUMN challan_staging.last_error IS 'Last DMS or DB error for this line';
COMMENT ON COLUMN challan_staging.inventory_line_id IS 'FK to vehicle_inventory_master after prepare_vehicle';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_challan_staging_inventory_line'
    ) THEN
        ALTER TABLE challan_staging
            ADD CONSTRAINT fk_challan_staging_inventory_line
            FOREIGN KEY (inventory_line_id) REFERENCES vehicle_inventory_master(inventory_line_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_challan_staging_batch ON challan_staging(challan_batch_id);
CREATE INDEX IF NOT EXISTS idx_challan_staging_status ON challan_staging(status);
