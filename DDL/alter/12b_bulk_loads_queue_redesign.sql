ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS job_id VARCHAR(64);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS parent_job_id VARCHAR(64);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS result_folder VARCHAR(512);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS action_taken BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS dealer_id INTEGER;
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS job_status VARCHAR(32) NOT NULL DEFAULT 'received';
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS processing_stage VARCHAR(64);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS source_path VARCHAR(1024);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS source_token VARCHAR(512);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS leased_until TIMESTAMPTZ;
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS worker_id VARCHAR(128);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS error_code VARCHAR(64);
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE bulk_loads ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

UPDATE bulk_loads
SET job_id = CONCAT('legacy-', id)
WHERE job_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_bulk_loads_job_id ON bulk_loads (job_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bulk_loads_dealer_source_token ON bulk_loads (dealer_id, source_token) WHERE source_token IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bulk_loads_dealer_created_at_desc ON bulk_loads (dealer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bulk_loads_dealer_status_created_at_desc ON bulk_loads (dealer_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bulk_loads_job_status_created_at_desc ON bulk_loads (job_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bulk_loads_leased_until ON bulk_loads (leased_until);
CREATE INDEX IF NOT EXISTS idx_bulk_loads_unresolved_hot ON bulk_loads (dealer_id, updated_at DESC) WHERE status IN ('Processing', 'Error', 'Rejected');

