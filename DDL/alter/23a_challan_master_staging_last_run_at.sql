-- Last automation run timestamp for Processed tab. Run after challan_master_staging exists.

ALTER TABLE challan_master_staging
    ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMPTZ;

COMMENT ON COLUMN challan_master_staging.last_run_at IS 'Updated when process/retry DMS batch completes (success or failure)';
