-- Row creation time for filtering recent staging rows (e.g. Processed tab, last N days).
-- Run after challan_staging exists.

ALTER TABLE challan_staging ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

COMMENT ON COLUMN challan_staging.created_at IS 'When the staging row was inserted';

CREATE INDEX IF NOT EXISTS idx_challan_staging_from_created ON challan_staging (from_dealer_id, created_at DESC);
