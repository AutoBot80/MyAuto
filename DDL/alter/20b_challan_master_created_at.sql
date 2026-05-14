-- Row creation time for admin usage charts and auditing. Run after challan_master exists.
ALTER TABLE challan_master ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;

COMMENT ON COLUMN challan_master.created_at IS 'UTC commit time when the challan row was inserted; NULL on legacy rows before this column';
