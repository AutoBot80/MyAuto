ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS vahan_application_id VARCHAR(128);

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS processing_session_id VARCHAR(128);

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS worker_id VARCHAR(128);

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS leased_until TIMESTAMPTZ;

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS last_error TEXT;

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMPTZ;

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

ALTER TABLE rto_queue
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE rto_queue
ALTER COLUMN status SET DEFAULT 'Queued';

ALTER TABLE rto_queue
ALTER COLUMN rto_status SET DEFAULT 'Pending';

CREATE INDEX IF NOT EXISTS idx_rto_queue_dealer_created_at ON rto_queue (dealer_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_rto_queue_session_status ON rto_queue (processing_session_id, status);
CREATE INDEX IF NOT EXISTS idx_rto_queue_lease ON rto_queue (leased_until);
